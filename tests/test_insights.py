"""Acceptance: teacher-insight pipeline (sections 9 and 10).

Covers minimum-cohort suppression, windowed aggregation, evidence-backed
inference, the observed/inferred/recommended separation, the no-surveillance
guarantees, the review + correction loop (which never edits a published
contract), gated raw-transcript access, and signal-quality metrics.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from pydantic import ValidationError

from ccl.contracts.schema import ContractStatus
from ccl.data.insight_models import EvaluationCaseRow, TeacherInsight
from ccl.data.tutor_models import InteractionEvent, TutorMessage, TutorSession
from ccl.insights import (
    MIN_COHORT,
    Aggregator,
    CorrectionKind,
    CorrectionService,
    InferredCluster,
    InsightService,
    InsightStatus,
    RawTranscriptAccessDenied,
    Recommendation,
    ReviewAction,
    TranscriptAccessService,
    WeeklyBriefBuilder,
    infer_cluster,
    signal_quality,
)
from ccl.insights.metrics import signal_quality as sq
from tests.conftest import make_valid_contract

NOW = dt.datetime.now(dt.timezone.utc)
WINDOW = (NOW - dt.timedelta(days=7), NOW + dt.timedelta(days=1))


def seed_concept(repo, concept_id, n_students, n_difficulty, when=NOW, course_id="math_demo", tag=""):
    """Create n distinct students who requested help on a concept; the first
    n_difficulty of them also hit a difficulty signal."""
    for i in range(n_students):
        sid = f"sess_{concept_id}_{tag}_{i}"
        repo.add(TutorSession(
            id=sid, course_id=course_id, student_id=f"stu_{i}",
            mode="practice", contract_version_id="cc_math51_unit3_v1",
        ))
        repo.add(InteractionEvent(
            id=f"ev_h_{concept_id}_{tag}_{i}", course_id=course_id, session_id=sid,
            type="tutor_help_requested", concept_id=concept_id, created_at=when,
        ))
        if i < n_difficulty:
            repo.add(InteractionEvent(
                id=f"ev_d_{concept_id}_{tag}_{i}", course_id=course_id, session_id=sid,
                type="high_support_reached", concept_id=concept_id, created_at=when,
            ))
    repo.flush()


# --- aggregation & suppression ---------------------------------------------

def test_below_cohort_is_suppressed(repo):
    seed_concept(repo, "concept_vectors", n_students=MIN_COHORT - 1, n_difficulty=3)
    aggs = Aggregator(repo).aggregate(make_valid_contract(), "math_demo", *WINDOW)
    assert aggs == []  # nothing surfaces below the minimum cohort


def test_at_cohort_surfaces(repo):
    seed_concept(repo, "concept_vectors", n_students=MIN_COHORT, n_difficulty=4)
    aggs = Aggregator(repo).aggregate(make_valid_contract(), "math_demo", *WINDOW)
    assert len(aggs) == 1
    assert aggs[0].cohort_size == MIN_COHORT
    assert aggs[0].difficulty_students == 4


def test_events_outside_window_excluded(repo):
    old = NOW - dt.timedelta(days=60)
    seed_concept(repo, "concept_vectors", n_students=MIN_COHORT, n_difficulty=5, when=old)
    aggs = Aggregator(repo).aggregate(make_valid_contract(), "math_demo", *WINDOW)
    assert aggs == []  # all events are outside the 7-day window


# --- inference --------------------------------------------------------------

def test_inferred_cluster_carries_evidence(repo):
    seed_concept(repo, "concept_vectors", n_students=6, n_difficulty=3)
    agg = Aggregator(repo).aggregate(make_valid_contract(), "math_demo", *WINDOW)[0]
    cluster = infer_cluster(agg, make_valid_contract())
    assert cluster.confidence == round(3 / 6, 2)
    assert cluster.sample_size == 6
    assert cluster.counterevidence_count == 3
    assert cluster.supporting_event_ids  # non-empty evidence
    assert cluster.source_refs  # linked to the preferred method's source


# --- view invariants (observed/inferred/recommended, cohort floor) ----------

def test_inference_below_cohort_cannot_be_constructed():
    with pytest.raises(ValidationError):
        InferredCluster(
            concept_id="c", concept_name="c", type="misconception_cluster",
            summary="x", confidence=0.9, sample_size=MIN_COHORT - 1,
        )


def test_recommendation_requires_dismiss_and_modify():
    with pytest.raises(ValidationError):
        Recommendation(text="do x", controls=["confirm"])


def test_only_allowed_insight_types():
    with pytest.raises(ValidationError):
        InferredCluster(
            concept_id="c", concept_name="c", type="demographic_breakdown",
            summary="x", confidence=0.5, sample_size=MIN_COHORT,
        )


# --- brief: no surveillance -------------------------------------------------

def test_brief_contains_no_student_identity(repo, audit):
    seed_concept(repo, "concept_vectors", n_students=8, n_difficulty=5)
    brief = WeeklyBriefBuilder(repo, audit).build(make_valid_contract(), "math_demo", *WINDOW)
    blob = brief.model_dump_json()
    assert "stu_" not in blob  # no student ids anywhere in the brief
    # No ranking field exists on the brief structure.
    assert "ranking" not in brief.model_dump()
    assert brief.misconception_clusters, "expected at least one surfaced cluster"


def test_brief_reports_rising_concepts_vs_prior_window(repo, audit):
    prior = (NOW - dt.timedelta(days=14), NOW - dt.timedelta(days=7))
    seed_concept(repo, "concept_vectors", 6, 2, when=NOW - dt.timedelta(days=10), tag="p")  # prior
    seed_concept(repo, "concept_vectors", 6, 5, when=NOW, tag="c")  # current, worse
    brief = WeeklyBriefBuilder(repo, audit).build(
        make_valid_contract(), "math_demo", *WINDOW, prior_start=prior[0], prior_end=prior[1]
    )
    assert "vectors" in brief.new_or_rising_concepts


# --- review loop ------------------------------------------------------------

def test_review_transitions(repo, audit):
    seed_concept(repo, "concept_vectors", 6, 4)
    WeeklyBriefBuilder(repo, audit).build(make_valid_contract(), "math_demo", *WINDOW)
    svc = InsightService(repo, audit)
    pending = repo.list(TeacherInsight, course_id="math_demo")
    assert pending
    svc.review(pending[0].id, ReviewAction.CONFIRM, actor_id="t1")
    assert repo.get(TeacherInsight, pending[0].id).status == InsightStatus.CONFIRMED.value


# --- correction loop: never edits a published contract ----------------------

def _publish_contract(contract_service):
    contract_service.create_draft(make_valid_contract())
    contract_service.validate("cc_math51_unit3_v1")
    contract_service.approve("cc_math51_unit3_v1", approved_by="t1")
    contract_service.publish("cc_math51_unit3_v1", actor_id="t1")


def test_correction_forks_draft_without_touching_published(repo, audit, contract_service, seeded_material):
    _publish_contract(contract_service)
    corr = CorrectionService(repo, audit, contract_service)
    corr.submit(
        course_id="math_demo", target_type="insight", target_id="insight_x",
        kind=CorrectionKind.WRONG_MISCONCEPTION, created_by="t1",
        resulting_action="draft_contract_change", base_contract_id="cc_math51_unit3_v1",
    )
    # Published contract is unchanged...
    published = contract_service.get_published("math_demo")
    assert published.version == 1 and published.status == ContractStatus.PUBLISHED
    # ...and a new *draft* version now exists for the teacher to edit.
    from ccl.data.models import CurriculumContractRow
    draft = repo.get(CurriculumContractRow, "math_demo_v2")
    assert draft is not None and draft.status == ContractStatus.DRAFT.value


def test_correction_creates_evaluation_case(repo, audit, contract_service, seeded_material):
    _publish_contract(contract_service)
    corr = CorrectionService(repo, audit, contract_service)
    corr.submit(
        course_id="math_demo", target_type="message", target_id="tmsg_1",
        kind=CorrectionKind.METHOD_MISMATCH, created_by="t1",
        resulting_action="evaluation_case",
        evaluation_case={
            "case_id": "from_correction", "contract_version_id": "cc_math51_unit3_v1",
            "mode": "practice", "student_message": "can I use the cross product?",
            "expected": {"must_not_use_method_ids": ["cross_product"]},
        },
    )
    cases = repo.list(EvaluationCaseRow, course_id="math_demo")
    assert len(cases) == 1 and cases[0].source == "teacher_correction"
    assert contract_service.get_published("math_demo").version == 1  # unchanged


# --- privacy-gated transcript access ----------------------------------------

def test_raw_transcript_access_requires_reason_and_is_audited(repo, audit):
    repo.add(TutorSession(id="s1", course_id="math_demo", student_id="stu_0",
                          mode="practice", contract_version_id="cc_math51_unit3_v1"))
    repo.flush()  # persist parent before child (Postgres enforces the FK)
    repo.add(TutorMessage(id="m1", session_id="s1", contract_version_id="cc_math51_unit3_v1",
                          student_message="help", response_text="hint", hint_level=2, outcome="answered"))
    repo.flush()
    svc = TranscriptAccessService(repo, audit)

    with pytest.raises(RawTranscriptAccessDenied):
        svc.access(session_id="s1", actor_id="t1", reason="", justification="student_escalation")
    with pytest.raises(RawTranscriptAccessDenied):
        svc.access(session_id="s1", actor_id="t1", reason="curious", justification="nosy")

    msgs = svc.access(session_id="s1", actor_id="t1", reason="student asked for help",
                      justification="student_escalation")
    assert len(msgs) == 1
    assert any(e.action == "privacy.raw_transcript_access" for e in audit.events())


def test_brief_generation_does_not_access_raw_transcripts(repo, audit):
    seed_concept(repo, "concept_vectors", 6, 4)
    WeeklyBriefBuilder(repo, audit).build(make_valid_contract(), "math_demo", *WINDOW)
    assert not any(e.action == "privacy.raw_transcript_access" for e in audit.events())


# --- signal quality ---------------------------------------------------------

def test_signal_quality_metrics(repo, audit, contract_service, seeded_material):
    seed_concept(repo, "concept_vectors", 8, 5)
    WeeklyBriefBuilder(repo, audit).build(make_valid_contract(), "math_demo", *WINDOW)
    svc = InsightService(repo, audit)
    rows = repo.list(TeacherInsight, course_id="math_demo")
    svc.review(rows[0].id, ReviewAction.CONFIRM, actor_id="t1")

    # One raw-access event for the privacy-exception count.
    repo.add(TutorSession(id="s9", course_id="math_demo", student_id="stu_0",
                          mode="practice", contract_version_id="cc_math51_unit3_v1"))
    repo.flush()
    TranscriptAccessService(repo, audit).access(
        session_id="s9", actor_id="t1", reason="escalation", justification="student_escalation"
    )

    m = signal_quality(repo, audit, "math_demo")
    assert m["insights_reviewed"] >= 1
    assert 0.0 <= m["actionability"] <= 1.0
    assert m["privacy_exceptions"] == 1
