"""Acceptance: retention, right-to-erasure, and export (sections 9.3, 12, 17).

Proves the separation that matters for a privacy review: raw student content is
redacted on a short clock while de-identified aggregates and the audit log
survive; erasing a data subject removes their learning data but not class-level
patterns; and both retention config and cross-subject actions are locked behind
admin rights at the Workspace boundary.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ccl.access import AccessDenied, Principal, Role, RoleGrant, Workspace
from ccl.data.insight_models import TeacherInsight
from ccl.data.privacy_models import DeletionRequest
from ccl.data.tutor_models import InteractionEvent, TutorMessage, TutorSession
from ccl.privacy import (
    REDACTED,
    DeletionService,
    ExportService,
    RetentionConfig,
    RetentionService,
)

NOW = dt.datetime.now(dt.timezone.utc)


def _age(days):
    return NOW - dt.timedelta(days=days)


def seed_message(repo, mid, student_id, age_days, session_id=None):
    session_id = session_id or f"sess_{mid}"
    if repo.get(TutorSession, session_id) is None:
        repo.add(TutorSession(id=session_id, course_id="math_demo", student_id=student_id,
                              mode="practice", contract_version_id="cc_v1"))
    repo.add(TutorMessage(
        id=mid, session_id=session_id, contract_version_id="cc_v1",
        student_message="please help me", response_text="here is a hint",
        hint_level=2, outcome="answered", citations_json='[{"a":1}]',
        policy_trace_json='{"mode":"practice"}', created_at=_age(age_days),
    ))
    repo.flush()


def seed_insight(repo, iid, age_days):
    repo.add(TeacherInsight(
        id=iid, course_id="math_demo", window_start="w0", window_end="w1",
        type="misconception_cluster", concept_id="concept_vectors",
        summary="pattern", confidence=0.8, sample_size=6, created_at=_age(age_days),
    ))
    repo.flush()


# --- retention --------------------------------------------------------------

def test_raw_content_redacted_but_trace_kept(repo, audit):
    seed_message(repo, "m_old", "stu_1", age_days=200)  # > raw(180), < trace(365)
    RetentionService(repo, audit).purge(now=NOW)
    msg = repo.get(TutorMessage, "m_old")
    assert msg is not None  # row (policy trace) survives
    assert msg.student_message == REDACTED and msg.response_text == REDACTED
    assert msg.policy_trace_json != "{}"  # trace preserved


def test_message_fully_deleted_after_trace_period(repo, audit):
    seed_message(repo, "m_ancient", "stu_1", age_days=400)  # > trace(365)
    RetentionService(repo, audit).purge(now=NOW)
    assert repo.get(TutorMessage, "m_ancient") is None


def test_recent_message_untouched(repo, audit):
    seed_message(repo, "m_new", "stu_1", age_days=10)
    RetentionService(repo, audit).purge(now=NOW)
    assert repo.get(TutorMessage, "m_new").student_message == "please help me"


def test_aggregate_insights_survive_raw_purge(repo, audit):
    seed_message(repo, "m_old", "stu_1", age_days=200)
    seed_insight(repo, "ins_recent", age_days=30)
    RetentionService(repo, audit).purge(now=NOW)
    # Raw content is redacted, but the de-identified aggregate remains.
    assert repo.get(TeacherInsight, "ins_recent") is not None
    assert repo.get(TutorMessage, "m_old").student_message == REDACTED


def test_audit_is_never_purged(repo, audit):
    audit.record(action="something", target_type="X", target_id="x1")
    before = len(audit.events())
    RetentionService(repo, audit).purge(now=NOW)
    # Purge adds its own audit event and deletes none.
    assert len(audit.events()) >= before + 1


# --- deletion (right to erasure) -------------------------------------------

def test_deletion_erases_subject_but_keeps_aggregates(repo, audit):
    seed_message(repo, "m1", "stu_1", age_days=5)
    repo.add(InteractionEvent(id="e1", course_id="math_demo", session_id="sess_m1",
                              type="tutor_help_requested", concept_id="concept_vectors"))
    seed_message(repo, "m2", "stu_2", age_days=5)  # a different student
    seed_insight(repo, "ins_1", age_days=5)
    repo.flush()

    req = DeletionService(repo, audit).request_and_execute(
        subject_user_id="stu_1", requested_by="admin1"
    )
    assert req.status == "completed"
    # stu_1's learning data is gone...
    assert repo.get(TutorMessage, "m1") is None
    assert repo.get(TutorSession, "sess_m1") is None
    assert repo.get(InteractionEvent, "e1") is None
    # ...but another student's data and the aggregate are untouched.
    assert repo.get(TutorMessage, "m2") is not None
    assert repo.get(TeacherInsight, "ins_1") is not None
    actions = {e.action for e in audit.events()}
    assert {"privacy.deletion.requested", "privacy.deletion.completed"} <= actions


# --- export -----------------------------------------------------------------

def test_export_gathers_subject_data(repo, audit):
    seed_message(repo, "m1", "stu_1", age_days=1)
    bundle = ExportService(repo, audit).export_user("stu_1", actor_id="stu_1")
    assert bundle["user_id"] == "stu_1"
    assert len(bundle["messages"]) == 1
    assert any(e.action == "privacy.export" for e in audit.events())


# --- Workspace gating -------------------------------------------------------

def _ws(repo, audit, user, role, course=None):
    return Workspace(Principal(user, "school_demo", (RoleGrant(role, course),)), repo, audit)


def test_student_can_export_own_but_not_others(repo, audit):
    seed_message(repo, "m1", "stu_1", age_days=1)
    ws = _ws(repo, audit, "stu_1", Role.STUDENT, "math_demo")
    assert ws.export_user_data("stu_1")["user_id"] == "stu_1"
    with pytest.raises(AccessDenied):
        ws.export_user_data("stu_2")


def test_student_cannot_delete_others_or_run_purge(repo, audit):
    ws = _ws(repo, audit, "stu_1", Role.STUDENT, "math_demo")
    with pytest.raises(AccessDenied):
        ws.request_data_deletion("stu_2")
    with pytest.raises(AccessDenied):
        ws.run_retention_purge(now=NOW)
    with pytest.raises(AccessDenied):
        ws.configure_retention(RetentionConfig())


def test_admin_can_configure_and_purge(repo, audit):
    seed_message(repo, "m_old", "stu_1", age_days=400)
    ws = _ws(repo, audit, "admin1", Role.ADMIN, None)
    ws.configure_retention(RetentionConfig())
    report = ws.run_retention_purge(now=NOW)
    assert report.messages_deleted == 1
