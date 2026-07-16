"""Acceptance: Gate 3 evaluation over the teacher-authored golden set (section 16).

This is the Tutor Runtime milestone's exit criterion. It proves:

1. the golden set meets the minimum size (>= 20 cases);
2. a compliant model clears the Gate-3 thresholds (method compliance >= 0.90,
   source support >= 0.95, answer leakage < 0.02) with a high answered rate;
3. an unaligned model is *caught*: the verifier flags its output and the
   orchestrator falls back, so the runtime never ships a violation. The honest
   failure signal is a collapse in answered_rate, not a spike in leakage.
"""

import pytest

from ccl.evals import (
    ANSWER_LEAKAGE_MAX,
    GOLDEN_MATH51,
    METHOD_COMPLIANCE_MIN,
    SOURCE_SUPPORTED_MIN,
    EvaluationHarness,
)
from ccl.providers import NaiveStubProvider, RuleAwareStubProvider
from ccl.providers.base import GenerateRequest, RetrievedChunk
from ccl.tutor.orchestrator import TutorOrchestrator
from ccl.tutor.verifier import verify
from tests.conftest import make_valid_contract


def _harness(repo, audit, provider):
    contract = make_valid_contract()
    orch = TutorOrchestrator(repo, audit, provider)
    return EvaluationHarness(orch, contract)


def test_golden_set_meets_minimum_size():
    assert len(GOLDEN_MATH51) >= 20


def test_gate3_passes_with_a_compliant_model(repo, audit, seeded_material):
    harness = _harness(repo, audit, RuleAwareStubProvider())
    report = harness.run_sync(GOLDEN_MATH51)

    assert len(report.outcomes) == len(GOLDEN_MATH51)
    assert report.metrics["method_compliance"] >= METHOD_COMPLIANCE_MIN
    assert report.metrics["source_supported"] >= SOURCE_SUPPORTED_MIN
    assert report.metrics["answer_leakage_rate"] < ANSWER_LEAKAGE_MAX
    # Every non-safety case is answered; only the one safety case falls back.
    assert report.metrics["answered_rate"] >= 0.90
    assert report.gate3_pass is True


def test_unaligned_model_is_caught_not_shipped(repo, audit, seeded_material):
    harness = _harness(repo, audit, NaiveStubProvider())
    report = harness.run_sync(GOLDEN_MATH51)

    # The runtime never delivers the unaligned model's answer: it verifies,
    # fails, revises once, fails again, and falls back. Nothing is "answered".
    assert report.metrics["answered_rate"] == 0.0
    assert report.metrics["fallback_rate"] == 1.0
    # The guardrail worked, so nothing actually leaked to the student...
    assert report.metrics["answer_leakage_rate"] < ANSWER_LEAKAGE_MAX
    # ...but the model still fails Gate 3: falling back on every turn, it can
    # never cite the required course sources, so source support collapses. That
    # is the honest "this model cannot meet the contract" signal.
    assert report.metrics["source_supported"] < SOURCE_SUPPORTED_MIN
    assert report.gate3_pass is False


def test_verifier_directly_rejects_an_unaligned_generation(seeded_material):
    # Unit-level proof that the fallback in the test above is driven by the
    # verifier catching the violation, not by luck.
    req = GenerateRequest(
        mode="practice",
        student_message="Can I just use the cross product here?",
        student_attempt="",
        target_hint_level=2,
        required_method_ids=["course_vector_method"],
        forbidden_method_ids=["cross_product"],
        method_names={"cross_product": "cross product"},
        forbidden_method_terms={"cross_product": ["cross product", "cross-product"]},
        allowed_chunks=[RetrievedChunk("material_notes_03", "p12", "linear combination")],
        require_citations=True,
        full_solution_allowed=False,
        boundary_message="This method is outside the current course sequence.",
    )
    import asyncio

    result = asyncio.run(NaiveStubProvider().generate(req))
    vr = verify(req, result, max_hint_level=5, boundary_required=True, injection_detected=False)

    assert vr.passed is False
    failed = {c.name for c in vr.failed}
    # At minimum it must catch the forbidden method and the missing citation.
    assert "method_compliance" in failed
    assert "answer_leakage" in failed
