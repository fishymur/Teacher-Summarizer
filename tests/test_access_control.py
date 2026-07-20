"""Acceptance: role-based access control and least privilege (section 17).

Covers the permission matrix, separation of duties (admin cannot touch
curriculum or student data), course scoping, audited denials, role
grant/revoke, and enforcement at the Workspace boundary.
"""

from __future__ import annotations

import pytest

from ccl.access import (
    AccessController,
    AccessDenied,
    Permission,
    Principal,
    Role,
    RoleGrant,
    RoleService,
    Workspace,
)
from ccl.contracts.schema import ContractStatus
from ccl.data import ContractService
from ccl.data.models import RoleAssignment
from ccl.data.tutor_models import TutorMessage, TutorSession
from ccl.insights.types import CorrectionKind, ReviewAction
from tests.conftest import make_valid_contract


def principal(user, *grants):
    return Principal(user, "school_demo", tuple(grants))


# --- permission matrix ------------------------------------------------------

def test_student_permissions(audit):
    ac = AccessController(audit)
    stu = principal("s1", RoleGrant(Role.STUDENT, "math_demo"))
    assert ac.can(stu, Permission.TUTOR_USE, "math_demo")
    assert ac.can(stu, Permission.TRANSCRIPT_ACCESS_OWN)
    assert not ac.can(stu, Permission.CONTRACT_PUBLISH, "math_demo")
    assert not ac.can(stu, Permission.INSIGHT_VIEW, "math_demo")


def test_admin_separation_of_duties(audit):
    ac = AccessController(audit)
    admin = principal("a1", RoleGrant(Role.ADMIN, None))
    assert ac.can(admin, Permission.ROLE_MANAGE)
    assert ac.can(admin, Permission.RETENTION_MANAGE)
    # Admin runs the plumbing but has no curriculum authority or student data.
    assert not ac.can(admin, Permission.CONTRACT_PUBLISH, "math_demo")
    assert not ac.can(admin, Permission.INSIGHT_REVIEW, "math_demo")
    assert not ac.can(admin, Permission.TRANSCRIPT_ACCESS_ESCALATED)


def test_course_scoping(audit):
    ac = AccessController(audit)
    teacher = principal("t1", RoleGrant(Role.TEACHER, "math_demo"))
    assert ac.can(teacher, Permission.CONTRACT_PUBLISH, "math_demo")
    assert not ac.can(teacher, Permission.CONTRACT_PUBLISH, "other_course")


def test_chair_is_cross_course(audit):
    ac = AccessController(audit)
    chair = principal("c1", RoleGrant(Role.CHAIR, None))
    assert ac.can(chair, Permission.CONTRACT_PUBLISH, "math_demo")
    assert ac.can(chair, Permission.CONTRACT_PUBLISH, "physics_demo")
    assert ac.can(chair, Permission.AUDIT_VIEW)


def test_denial_raises_and_audits(audit):
    ac = AccessController(audit)
    stu = principal("s1", RoleGrant(Role.STUDENT, "math_demo"))
    with pytest.raises(AccessDenied):
        ac.require(stu, Permission.CONTRACT_PUBLISH, "math_demo", action="publish")
    assert any(e.action == "access.denied" for e in audit.events())


# --- role service -----------------------------------------------------------

def test_roleservice_grant_and_load(repo, audit):
    svc = RoleService(repo, audit)
    svc.grant("t1", Role.TEACHER, "math_demo", actor_id="admin1")
    p = svc.load_principal("t1")
    assert p.roles == {Role.TEACHER}
    assert p.grants[0].course_id == "math_demo"
    assert any(e.action == "role.grant" for e in audit.events())


def test_roleservice_revoke(repo, audit):
    svc = RoleService(repo, audit)
    row = svc.grant("t1", Role.TEACHER, "math_demo", actor_id="admin1")
    svc.revoke(row.id, actor_id="admin1")
    assert svc.load_principal("t1").grants == ()
    assert any(e.action == "role.revoke" for e in audit.events())


# --- Workspace enforcement --------------------------------------------------

def _teacher_ws(repo, audit, course="math_demo"):
    return Workspace(principal("t1", RoleGrant(Role.TEACHER, course)), repo, audit)


def test_workspace_teacher_can_publish(repo, audit, seeded_material):
    ws = _teacher_ws(repo, audit)
    ws.author_contract(make_valid_contract())
    ws.validate_contract("cc_math51_unit3_v1", "math_demo")
    ws.approve_contract("cc_math51_unit3_v1", "math_demo")
    ws.publish_contract("cc_math51_unit3_v1", "math_demo")
    published = ContractService(repo, audit).get_published("math_demo")
    assert published.version == 1 and published.status == ContractStatus.PUBLISHED


def test_workspace_student_cannot_author(repo, audit, seeded_material):
    ws = Workspace(principal("s1", RoleGrant(Role.STUDENT, "math_demo")), repo, audit)
    with pytest.raises(AccessDenied):
        ws.author_contract(make_valid_contract())
    assert any(e.action == "access.denied" for e in audit.events())


def test_workspace_teacher_scoped_to_own_course(repo, audit, seeded_material):
    # Teacher of another course cannot publish this one.
    ws = _teacher_ws(repo, audit, course="other_course")
    with pytest.raises(AccessDenied):
        ws.publish_contract("cc_math51_unit3_v1", "math_demo")


def _seed_session(repo, session_id, student_id):
    repo.add(TutorSession(id=session_id, course_id="math_demo", student_id=student_id,
                          mode="practice", contract_version_id="cc_math51_unit3_v1"))
    repo.flush()  # persist parent before child (Postgres enforces the FK)
    repo.add(TutorMessage(id=f"m_{session_id}", session_id=session_id,
                          contract_version_id="cc_math51_unit3_v1",
                          student_message="help", response_text="hint",
                          hint_level=2, outcome="answered"))
    repo.flush()


def test_student_reads_own_but_not_others_transcript(repo, audit):
    _seed_session(repo, "sess_a", "s1")
    _seed_session(repo, "sess_b", "s2")
    ws = Workspace(principal("s1", RoleGrant(Role.STUDENT, "math_demo")), repo, audit)

    own = ws.access_transcript("sess_a")
    assert len(own) == 1
    # Reading own transcript is the default, not a privacy exception.
    assert not any(e.action == "privacy.raw_transcript_access" for e in audit.events())

    with pytest.raises(AccessDenied):
        ws.access_transcript("sess_b")


def test_teacher_escalated_transcript_is_audited(repo, audit):
    _seed_session(repo, "sess_a", "s1")
    ws = _teacher_ws(repo, audit)
    msgs = ws.access_transcript(
        "sess_a", reason="student requested help", justification="student_escalation"
    )
    assert len(msgs) == 1
    assert any(e.action == "privacy.raw_transcript_access" for e in audit.events())


def test_only_admin_can_manage_roles(repo, audit):
    student_ws = Workspace(principal("s1", RoleGrant(Role.STUDENT, "math_demo")), repo, audit)
    with pytest.raises(AccessDenied):
        student_ws.grant_role("s2", Role.TEACHER, "math_demo")

    admin_ws = Workspace(principal("a1", RoleGrant(Role.ADMIN, None)), repo, audit)
    row = admin_ws.grant_role("t9", Role.TEACHER, "math_demo")
    assert repo.get(RoleAssignment, row.id) is not None
    assert any(e.action == "role.grant" for e in audit.events())
