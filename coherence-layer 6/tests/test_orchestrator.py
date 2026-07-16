"""Orchestrator tests (section 8 pipeline), end to end and headless."""

import asyncio

from ccl.data import AuditLog
from ccl.data.tutor_models import InteractionEvent, TutorMessage
from ccl.providers import GenerateResult, ResultCitation, ScriptedProvider
from ccl.tutor import SAFE_FALLBACK, SessionContext, TutorOrchestrator


def _ctx():
    return SessionContext(
        tenant_id="school_demo", course_id="math_demo", student_id="stu_1",
        mode="practice", concept_ids=["concept_vectors"], current_unit="unit_3",
    )


def test_practice_boundary_turn_is_compliant(repo, orchestrator, published_contract):
    turn = asyncio.run(
        orchestrator.respond(published_contract, _ctx(), "Can I just use the cross product here?")
    )
    assert turn.outcome == "answered"
    assert turn.verifier.passed
    assert turn.discloses_boundary is True
    assert "cross_product" in turn.policy.forbidden_method_ids
    assert turn.hint_level <= turn.policy.max_hint_level
    # Persistence + events + audit.
    assert repo.list(TutorMessage), "a TutorMessage should be stored"
    assert repo.list(InteractionEvent), "interaction events should be emitted"
    assert any(e.action == "tutor.session.create" for e in AuditLog(repo).events())


def test_solution_request_without_attempt_is_diagnostic(repo, orchestrator, published_contract):
    turn = asyncio.run(
        orchestrator.respond(published_contract, _ctx(), "Just give me the answer to problem 3.")
    )
    assert turn.hint_level <= 2  # diagnostic rung, not a solution
    assert turn.outcome == "answered"


def test_verifier_failure_twice_falls_back(repo, audit, published_contract):
    bad = GenerateResult(
        response_text="Sure, use the cross product; the answer is 42.",
        citations=[ResultCitation("material_notes_03", "p999", "nope")],
        hint_level=6, discloses_boundary=False,
    )
    orch = TutorOrchestrator(repo, audit, ScriptedProvider([bad, bad]))
    turn = asyncio.run(
        orch.respond(published_contract, _ctx(), "Can I use the cross product here?")
    )
    assert turn.outcome == "fallback"
    assert turn.escalation_offered is True
    assert turn.response_text == SAFE_FALLBACK


def test_revise_once_then_succeeds(repo, audit, published_contract):
    bad = GenerateResult(
        response_text="Sure, use the cross product; the answer is 42.",
        citations=[ResultCitation("material_notes_03", "p999", "nope")],
        hint_level=6, discloses_boundary=False,
    )
    good = GenerateResult(
        response_text="This method is outside the current course sequence. Use the approved "
                      "course method instead. What relationship can you write?",
        citations=[ResultCitation("material_notes_03", "p12", "The course vector method")],
        hint_level=2, discloses_boundary=True,
    )
    orch = TutorOrchestrator(repo, audit, ScriptedProvider([bad, good]))
    turn = asyncio.run(
        orch.respond(published_contract, _ctx(), "Can I use the cross product here?")
    )
    assert turn.outcome == "revised"
    assert turn.verifier.passed


def test_unsafe_message_routes_to_safety_fallback(repo, orchestrator, published_contract):
    turn = asyncio.run(
        orchestrator.respond(published_contract, _ctx(), "I want to hurt myself")
    )
    assert turn.outcome == "safety_fallback"
    assert any(
        e.action == "tutor.safety.escalation" for e in AuditLog(repo).events()
    )
