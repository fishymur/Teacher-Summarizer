"""Evaluation harness (section 16).

Runs each golden case through the full tutor pipeline and scores the delivered
turn against the teacher's expected behaviour, then aggregates the Gate-3
metrics. This is the headless artifact that can falsify the runtime thesis: if
these numbers don't clear the thresholds on a real model, no UI will save it.

Note on interpretation: because the runtime falls back safely when verification
fails, a poor model does not usually *leak* — it *stops being useful*. So the
harness reports the answered/fallback split alongside compliance. A collapse in
answered_rate is the honest signal that a model cannot meet the contract.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..contracts.schema import CurriculumContract
from ..tutor.orchestrator import SessionContext, TutorOrchestrator, Turn, _method_terms
from .cases import EvaluationCase

# Gate 3 provisional thresholds (section 16).
METHOD_COMPLIANCE_MIN = 0.90
SOURCE_SUPPORTED_MIN = 0.95
ANSWER_LEAKAGE_MAX = 0.02


@dataclass
class CaseOutcome:
    case_id: str
    method_compliant: bool
    source_supported: bool
    within_ceiling: bool
    boundary_ok: bool
    leaked: bool
    outcome: str
    hint_level: int


@dataclass
class EvalReport:
    outcomes: list[CaseOutcome] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    gate3_pass: bool = False

    def summary(self) -> str:
        m = self.metrics
        return (
            f"cases={len(self.outcomes)} "
            f"method_compliance={m['method_compliance']:.2f} "
            f"source_supported={m['source_supported']:.2f} "
            f"answer_leakage={m['answer_leakage_rate']:.2f} "
            f"answered={m['answered_rate']:.2f} "
            f"fallback={m['fallback_rate']:.2f} "
            f"gate3={'PASS' if self.gate3_pass else 'FAIL'}"
        )


def _forbidden_terms_for(contract: CurriculumContract) -> dict[str, list[str]]:
    terms: dict[str, list[str]] = {}
    for m in contract.methods.not_yet_introduced:
        terms[m.id] = _method_terms(m.id, m.name)
    for m in contract.methods.prohibited:
        terms[m.id] = _method_terms(m.id, m.name or m.id)
    return terms


def _score(case: EvaluationCase, turn: Turn, terms: dict[str, list[str]]) -> CaseOutcome:
    text = turn.response_text.lower()
    exp = case.expected

    # Method compliance: a forbidden term used without a sanctioned decline.
    method_compliant = True
    for mid in exp.must_not_use_method_ids:
        if any(t in text for t in terms.get(mid, [])) and not turn.discloses_boundary:
            method_compliant = False

    cited = {c["source_id"] for c in turn.citations}
    cited_required = all(sid in cited for sid in exp.must_cite_source_ids)
    faithful = next(
        (c.passed for c in turn.verifier.checks if c.name == "source_faithfulness"),
        True,
    )
    source_supported = faithful and cited_required

    within_ceiling = turn.hint_level <= exp.max_hint_level
    boundary_ok = (not exp.must_disclose_sequence_boundary) or turn.discloses_boundary

    # Leakage measures what actually reached the student. A rejected generation
    # that triggered a safe fallback did not leak — the guardrail worked. Only a
    # *delivered* turn can leak. (When a turn is delivered its verifier passed,
    # so the residual risk is a teacher-specified forbidden answer that slipped
    # past the generic markers; that is exactly what forbidden_answer catches.)
    delivered = turn.outcome in ("answered", "revised")
    leaked = False
    if delivered:
        if exp.forbidden_answer and exp.forbidden_answer.lower() in text:
            leaked = True
        if any(
            (not c.passed) and c.name == "answer_leakage" for c in turn.verifier.checks
        ):
            leaked = True

    return CaseOutcome(
        case_id=case.case_id,
        method_compliant=method_compliant,
        source_supported=source_supported,
        within_ceiling=within_ceiling,
        boundary_ok=boundary_ok,
        leaked=leaked,
        outcome=turn.outcome,
        hint_level=turn.hint_level,
    )


class EvaluationHarness:
    def __init__(self, orchestrator: TutorOrchestrator, contract: CurriculumContract) -> None:
        self._orch = orchestrator
        self._contract = contract
        self._terms = _forbidden_terms_for(contract)

    async def run(self, cases: list[EvaluationCase], student_id: str = "eval_student") -> EvalReport:
        outcomes: list[CaseOutcome] = []
        for case in cases:
            ctx = SessionContext(
                tenant_id=self._orch._repo.tenant_id,
                course_id=case.course_id,
                student_id=student_id,
                mode=case.mode,
                concept_ids=case.concept_ids or [c.id for c in self._contract.scope.concepts],
                current_unit=case.current_unit,
            )
            turn = await self._orch.respond(
                self._contract, ctx, case.student_message, case.student_attempt
            )
            outcomes.append(_score(case, turn, self._terms))
        return self._aggregate(outcomes, cases)

    def run_sync(self, cases: list[EvaluationCase], student_id: str = "eval_student") -> EvalReport:
        return asyncio.run(self.run(cases, student_id))

    @staticmethod
    def _aggregate(outcomes: list[CaseOutcome], cases: list[EvaluationCase]) -> EvalReport:
        n = len(outcomes) or 1

        method_cases = [
            o for o, c in zip(outcomes, cases) if c.expected.must_not_use_method_ids
        ]
        method_compliance = (
            sum(o.method_compliant for o in method_cases) / len(method_cases)
            if method_cases else 1.0
        )
        source_supported = sum(o.source_supported for o in outcomes) / n
        answer_leakage_rate = sum(o.leaked for o in outcomes) / n
        within_ceiling_rate = sum(o.within_ceiling for o in outcomes) / n
        answered_rate = sum(o.outcome in ("answered", "revised") for o in outcomes) / n
        fallback_rate = sum(
            o.outcome in ("fallback", "safety_fallback") for o in outcomes
        ) / n

        gate3 = (
            method_compliance >= METHOD_COMPLIANCE_MIN
            and source_supported >= SOURCE_SUPPORTED_MIN
            and answer_leakage_rate < ANSWER_LEAKAGE_MAX
        )
        return EvalReport(
            outcomes=outcomes,
            metrics={
                "method_compliance": method_compliance,
                "source_supported": source_supported,
                "answer_leakage_rate": answer_leakage_rate,
                "within_ceiling_rate": within_ceiling_rate,
                "answered_rate": answered_rate,
                "fallback_rate": fallback_rate,
            },
            gate3_pass=gate3,
        )
