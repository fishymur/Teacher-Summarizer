"""Tutor orchestrator (section 8 request pipeline).

Ties together the Milestone-1 policy engine with retrieval, planning,
generation, and verification. The flow: classify -> policy decision -> retrieve
approved evidence -> plan the hint level -> generate -> verify -> (revise once)
-> safe fallback -> persist and emit aggregate events.

The orchestrator never relaxes a policy decision. It decides *what the runtime
may do*; the model decides what to say within those limits; the verifier proves
it stayed inside them before anything is returned.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from ..contracts.schema import ContractStatus, CurriculumContract
from ..data.audit import AuditLog
from ..data.models import Course  # noqa: F401 (ensures mapper import order)
from ..data.repository import TenantRepository
from ..data.tutor_models import InteractionEvent, TutorMessage, TutorSession
from ..policy.engine import PolicyDecision, PolicyEngine, RequestContext
from ..providers.base import GenerateRequest, LLMProvider
from .classifier import RequestType, classify
from .injection import scan_chunks
from .planner import plan_hint
from .retrieval import KeywordRetriever
from .verifier import VerifierResult, verify

SAFE_FALLBACK = (
    "I can help with the method used in this course, but I do not have enough "
    "approved material to answer this reliably. I can show the relevant source I "
    "found or package the question for your teacher."
)
SAFETY_FALLBACK = (
    "It sounds like something serious might be going on. I can't help with that "
    "here, but I can connect you with your teacher or a trusted adult right now."
)


@dataclass
class SessionContext:
    tenant_id: str
    course_id: str
    student_id: str
    mode: str
    concept_ids: list[str] = field(default_factory=list)
    current_unit: str | None = None
    unit_order: list[str] | None = None
    session_id: str | None = None


@dataclass
class Turn:
    response_text: str
    hint_level: int
    citations: list[dict]
    discloses_boundary: bool
    outcome: str  # answered | revised | fallback | safety_fallback
    request_type: str
    policy: PolicyDecision
    verifier: VerifierResult
    escalation_offered: bool
    session_id: str
    message_id: str


def _method_terms(method_id: str, name: str) -> list[str]:
    n = name.lower()
    variants = {n, n.replace(" ", "-"), n.replace(" ", ""), method_id.replace("_", " ")}
    return sorted(v for v in variants if v)


class TutorOrchestrator:
    def __init__(
        self,
        repo: TenantRepository,
        audit: AuditLog,
        provider: LLMProvider,
        engine: PolicyEngine | None = None,
        registry=None,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._provider = provider
        self._engine = engine or PolicyEngine()
        self._retriever = KeywordRetriever(repo)
        # Optional provider governance. When supplied, the runtime refuses to
        # call a model that is not registered and approved (fail closed).
        self._registry = registry

    async def respond(
        self,
        contract: CurriculumContract,
        ctx: SessionContext,
        student_message: str,
        student_attempt: str = "",
        attempt_image: dict | None = None,
    ) -> Turn:
        contract = self._ensure_published(contract)
        session = self._ensure_session(contract, ctx)
        history = self._load_history(session.id)

        request_type = classify(student_message)

        # Safety short-circuit: never route self-harm content to the model.
        if request_type is RequestType.UNSAFE:
            return self._safety_turn(contract, ctx, session, student_message)

        concept_ids = ctx.concept_ids or [c.id for c in contract.scope.concepts]
        decision = self._engine.decide(
            contract,
            RequestContext(
                mode=ctx.mode,
                concept_ids=concept_ids,
                current_unit=ctx.current_unit,
                unit_order=ctx.unit_order,
            ),
        )

        chunks = self._retriever.retrieve(
            f"{student_message} {student_attempt}", decision.allowed_source_ids, k=3
        )

        # Untrusted-content channel: an approved document may contain a hidden
        # injection. Flag it, audit it, and mark the turn injection-suspect. The
        # policy decision above is unaffected — it comes from the contract, not
        # from document text — so forbidden methods stay forbidden regardless.
        flagged_chunks = scan_chunks(chunks)
        if flagged_chunks:
            self._audit.record(
                action="security.prompt_injection_detected",
                target_type="TutorSession",
                target_id=session.id,
                actor_id=ctx.student_id,
                detail=f"document_injection chunks={flagged_chunks}",
            )

        method_names = {
            m.id: m.name for m in contract.methods.preferred
        }
        forbidden_terms: dict[str, list[str]] = {}
        for m in contract.methods.not_yet_introduced:
            method_names[m.id] = m.name
            forbidden_terms[m.id] = _method_terms(m.id, m.name)
        for m in contract.methods.prohibited:
            nm = m.name or m.id
            method_names[m.id] = nm
            forbidden_terms[m.id] = _method_terms(m.id, nm)

        # Boundary detection: did the student ask for a restricted method?
        boundary_required = False
        boundary_message = None
        lower_msg = student_message.lower()
        for mid in decision.forbidden_method_ids:
            if any(term in lower_msg for term in forbidden_terms.get(mid, [])):
                boundary_required = True
                boundary_message = decision.student_explanations.get(
                    f"rule_{mid}_not_yet"
                ) or next(iter(decision.student_explanations.values()), None)
                break

        # A photo of handwritten work counts as an attempt for hint gating.
        effective_attempt = student_attempt or ("[handwritten attempt attached]" if attempt_image else "")

        plan = plan_hint(
            request_type=request_type,
            mode_ceiling=decision.max_hint_level,
            require_student_attempt=contract.pedagogy.require_student_attempt,
            student_attempt=effective_attempt,
        )
        target_level = min(plan.target_hint_level, decision.max_hint_level)

        req = GenerateRequest(
            mode=ctx.mode,
            student_message=student_message,
            student_attempt=student_attempt,
            target_hint_level=target_level,
            required_method_ids=decision.required_method_ids,
            forbidden_method_ids=decision.forbidden_method_ids,
            method_names=method_names,
            forbidden_method_terms=forbidden_terms,
            allowed_chunks=chunks,
            require_citations=contract.source_policy.require_citations,
            full_solution_allowed=decision.full_solution_allowed,
            boundary_message=boundary_message,
            attempt_image=attempt_image,
            history=history,
        )

        injection = (request_type is RequestType.PROMPT_INJECTION) or bool(flagged_chunks)

        # Provider governance gate: refuse to call an unapproved model.
        if self._registry is not None:
            self._registry.ensure_usable(self._provider.capabilities().model_id)

        result = await self._provider.generate(req)
        vr = verify(
            req, result, max_hint_level=decision.max_hint_level,
            boundary_required=boundary_required, injection_detected=injection,
        )
        outcome = "answered"

        if not vr.passed:
            # Revise once with structured feedback.
            req.revision_feedback = vr.feedback()
            result = await self._provider.generate(req)
            vr = verify(
                req, result, max_hint_level=decision.max_hint_level,
                boundary_required=boundary_required, injection_detected=injection,
            )
            outcome = "revised"

        escalation_offered = False
        if not vr.passed:
            # Still failing: safe fallback, offer escalation.
            outcome = "fallback"
            escalation_offered = True
            response_text = SAFE_FALLBACK
            hint_level = 1
            citations: list[dict] = []
            discloses = False
        else:
            response_text = result.response_text
            hint_level = result.hint_level
            citations = [
                {"source_id": c.source_id, "anchor": c.anchor, "quote": c.quote}
                for c in result.citations
            ]
            discloses = result.discloses_boundary

        message = self._persist(
            contract, ctx, session, student_message, response_text, hint_level,
            outcome, citations, decision, vr, result.cost_usd, result.latency_ms,
        )
        self._emit_events(
            ctx, session, concept_ids, hint_level,
            outcome=outcome, discloses_boundary=discloses,
            has_attempt=bool(student_attempt.strip()) or bool(attempt_image),
            request_type=request_type, ceiling=decision.max_hint_level,
        )

        return Turn(
            response_text=response_text,
            hint_level=hint_level,
            citations=citations,
            discloses_boundary=discloses,
            outcome=outcome,
            request_type=request_type.value,
            policy=decision,
            verifier=vr,
            escalation_offered=escalation_offered,
            session_id=session.id,
            message_id=message.id,
        )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _ensure_published(contract: CurriculumContract) -> CurriculumContract:
        if contract.status != ContractStatus.PUBLISHED:
            return contract.model_copy(update={"status": ContractStatus.PUBLISHED})
        return contract

    def _ensure_session(
        self, contract: CurriculumContract, ctx: SessionContext
    ) -> TutorSession:
        if ctx.session_id:
            existing = self._repo.get(TutorSession, ctx.session_id)
            if existing:
                return existing
        session = TutorSession(
            id=ctx.session_id or f"sess_{uuid.uuid4().hex[:12]}",
            course_id=ctx.course_id,
            student_id=ctx.student_id,
            mode=ctx.mode,
            contract_version_id=contract.contract_id,
        )
        self._repo.add(session)
        self._repo.flush()
        ctx.session_id = session.id
        self._audit.record(
            action="tutor.session.create",
            target_type="TutorSession",
            target_id=session.id,
            actor_id=ctx.student_id,
        )
        return session

    def _load_history(self, session_id: str, max_turns: int = 3) -> list[dict]:
        """Recent (student, tutor) exchanges for this session, oldest first.
        Context for generation only — governance is recomputed each turn."""
        msgs = self._repo.list(TutorMessage, session_id=session_id)
        msgs = sorted(msgs, key=lambda m: m.created_at or 0)[-max_turns:]
        history: list[dict] = []
        for m in msgs:
            if m.student_message:
                history.append({"role": "student", "text": m.student_message})
            if m.response_text:
                history.append({"role": "tutor", "text": m.response_text})
        return history

    def _safety_turn(
        self, contract, ctx, session, student_message
    ) -> Turn:
        message = self._persist(
            contract, ctx, session, student_message, SAFETY_FALLBACK, 1,
            "safety_fallback", [], None, VerifierResult(), 0.0, 0.0,
        )
        self._audit.record(
            action="tutor.safety.escalation",
            target_type="TutorSession",
            target_id=session.id,
            actor_id=ctx.student_id,
        )
        return Turn(
            response_text=SAFETY_FALLBACK, hint_level=1, citations=[],
            discloses_boundary=False, outcome="safety_fallback",
            request_type=RequestType.UNSAFE.value, policy=None,
            verifier=VerifierResult(), escalation_offered=True,
            session_id=session.id, message_id=message.id,
        )

    def _persist(
        self, contract, ctx, session, student_message, response_text, hint_level,
        outcome, citations, decision, vr, cost, latency,
    ) -> TutorMessage:
        policy_trace = {} if decision is None else {
            "mode": decision.mode,
            "max_hint_level": decision.max_hint_level,
            "required_method_ids": decision.required_method_ids,
            "forbidden_method_ids": decision.forbidden_method_ids,
            "allowed_source_ids": decision.allowed_source_ids,
            "full_solution_allowed": decision.full_solution_allowed,
            "explanations": decision.explanations,
        }
        message = TutorMessage(
            id=f"tmsg_{uuid.uuid4().hex[:12]}",
            session_id=session.id,
            contract_version_id=contract.contract_id,
            student_message=student_message,
            response_text=response_text,
            hint_level=hint_level,
            outcome=outcome,
            citations_json=json.dumps(citations),
            policy_trace_json=json.dumps(policy_trace),
            verifier_result_json=json.dumps(vr.to_dict()),
            cost_usd=cost,
            latency_ms=latency,
        )
        self._repo.add(message)
        self._repo.flush()
        return message

    def _emit_events(
        self, ctx, session, concept_ids, hint_level, *,
        outcome, discloses_boundary, has_attempt, request_type, ceiling,
    ) -> None:
        """Emit aggregate interaction events. Never a synchronous per-student
        diagnosis (section 8, step 11) — these feed the insight pipeline, which
        only surfaces patterns above the minimum cohort threshold."""
        concept = concept_ids[0] if concept_ids else None
        types = ["tutor_help_requested", "hint_level_delivered"]
        if has_attempt:
            types.append("student_attempt_submitted")
        if request_type is RequestType.SOLUTION_REQUEST:
            types.append("full_solution_requested")
        if hint_level >= max(ceiling, 1):
            types.append("high_support_reached")
        if discloses_boundary:
            types.append("boundary_disclosed")
        if outcome == "fallback":
            types.append("fallback_delivered")
        if request_type is RequestType.OFF_TOPIC:
            types.append("out_of_scope_question")

        detail = json.dumps({"hint_level": hint_level, "outcome": outcome})
        for etype in types:
            self._repo.add(
                InteractionEvent(
                    id=f"ievt_{uuid.uuid4().hex[:12]}",
                    course_id=ctx.course_id,
                    session_id=session.id,
                    type=etype,
                    concept_id=concept,
                    detail=detail,
                )
            )
        self._repo.flush()
