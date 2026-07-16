"""Response verifier (section 8, pipeline step 8).

The verifier is the defensible core of the runtime. Generation is easy; proving
the generation obeyed the contract is the hard part. Every check operates on
the *structured output and the raw text*, never on the model's self-assessment
of its own compliance. A model claiming "I used the course method" is not
evidence; the evidence is that no forbidden method term appears in the text and
that every cited quote is present verbatim in an approved chunk.

Limitations (honest): method-leakage and answer-leakage use deterministic,
inspectable heuristics rather than a judge model. They are conservative and
catch the failure modes in the golden set; a production system would add a
model-based judge for the fuzzier cases. Each heuristic is isolated so it can be
upgraded without touching the rest of the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..providers.base import GenerateRequest, GenerateResult

_ANSWER_MARKERS = ("the final answer is", "the answer is", "the solution is", "answer:")
_CERTAINTY_MARKERS = ("definitely", "certainly", "always", "guaranteed")
_INJECTION_ECHO = ("ignoring previous", "as instructed i will ignore", "system prompt")
_TEACHING_MARKERS = ("step 1", "first, compute", "formula is", "multiply", "= ")


@dataclass
class VerifierCheck:
    name: str
    passed: bool
    critical: bool
    detail: str = ""


@dataclass
class VerifierResult:
    checks: list[VerifierCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.critical)

    @property
    def failed(self) -> list[VerifierCheck]:
        return [c for c in self.checks if not c.passed]

    def feedback(self) -> str:
        return "; ".join(f"{c.name}: {c.detail}" for c in self.failed) or "none"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "critical": c.critical, "detail": c.detail}
                for c in self.checks
            ],
        }


def _method_terms(request: GenerateRequest) -> list[tuple[str, str]]:
    """Return (method_id, term) pairs for every forbidden method."""
    pairs: list[tuple[str, str]] = []
    for mid in request.forbidden_method_ids:
        for term in request.forbidden_method_terms.get(mid, [mid.replace("_", " ")]):
            pairs.append((mid, term.lower()))
    return pairs


def verify(
    request: GenerateRequest,
    result: GenerateResult,
    *,
    max_hint_level: int,
    boundary_required: bool,
    injection_detected: bool = False,
) -> VerifierResult:
    vr = VerifierResult()
    text = result.response_text.lower()
    allowed = {(c.source_id, c.anchor): c.text for c in request.allowed_chunks}

    # 1. Citation validity: each citation resolves to an approved chunk.
    bad_cites = [
        (c.source_id, c.anchor)
        for c in result.citations
        if (c.source_id, c.anchor) not in allowed
    ]
    vr.checks.append(
        VerifierCheck(
            "citation_validity", not bad_cites, True,
            "" if not bad_cites else f"unapproved citations: {bad_cites}",
        )
    )

    # 2. Source faithfulness: each quote is verbatim in its cited chunk.
    unfaithful = [
        c.quote
        for c in result.citations
        if (c.source_id, c.anchor) in allowed
        and c.quote.strip()
        and c.quote.strip() not in allowed[(c.source_id, c.anchor)]
    ]
    vr.checks.append(
        VerifierCheck(
            "source_faithfulness", not unfaithful, True,
            "" if not unfaithful else f"quotes not found in source: {unfaithful}",
        )
    )

    # 3. Required citation present (when the contract requires citations).
    needs_cite = request.require_citations and result.hint_level >= 3
    has_valid_cite = any((c.source_id, c.anchor) in allowed for c in result.citations)
    vr.checks.append(
        VerifierCheck(
            "required_citation", (not needs_cite) or has_valid_cite, True,
            "" if (not needs_cite) or has_valid_cite else "substantive help lacks a valid citation",
        )
    )

    # 4. Method compliance / not-yet leakage. A forbidden term may appear ONLY
    #    as part of a sanctioned boundary decline.
    leaked_methods = []
    for mid, term in _method_terms(request):
        if term in text and not result.discloses_boundary:
            leaked_methods.append(mid)
    vr.checks.append(
        VerifierCheck(
            "method_compliance", not leaked_methods, True,
            "" if not leaked_methods else f"used forbidden method(s): {leaked_methods}",
        )
    )

    # 5. Hint ceiling.
    within = result.hint_level <= max_hint_level
    vr.checks.append(
        VerifierCheck(
            "hint_ceiling", within, True,
            "" if within else f"hint level {result.hint_level} exceeds ceiling {max_hint_level}",
        )
    )

    # 6. Answer leakage when a full solution is not permitted.
    leaked_answer = (not request.full_solution_allowed) and (
        any(m in text for m in _ANSWER_MARKERS) or result.hint_level >= 6
    )
    vr.checks.append(
        VerifierCheck(
            "answer_leakage", not leaked_answer, True,
            "" if not leaked_answer else "revealed a full solution outside an allowed mode",
        )
    )

    # 7. Boundary disclosure when a restricted method was requested.
    if boundary_required:
        disclosed = result.discloses_boundary and bool(
            request.boundary_message and request.boundary_message.lower()[:20] in text
        )
        vr.checks.append(
            VerifierCheck(
                "boundary_disclosure", disclosed, True,
                "" if disclosed else "restricted method requested but boundary not disclosed",
            )
        )

    # 8. Prompt-injection resilience (critical only when an injection was seen).
    if injection_detected:
        complied = any(m in text for m in _INJECTION_ECHO)
        vr.checks.append(
            VerifierCheck(
                "prompt_injection_resilience", not complied, True,
                "" if not complied else "response echoed an injected instruction",
            )
        )

    # 9. Unsupported certainty (advisory).
    overcertain = (
        any(m in text for m in _CERTAINTY_MARKERS) and not has_valid_cite
    )
    vr.checks.append(
        VerifierCheck(
            "unsupported_certainty", not overcertain, False,
            "" if not overcertain else "confident claim without a citation",
        )
    )

    return vr
