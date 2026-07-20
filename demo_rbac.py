"""End-to-end demo of Milestone 4a: role-based access control.

Shows an admin granting roles, a teacher publishing within scope, and the
boundary refusing (and auditing) everything that violates least privilege:
a student authoring a contract, a teacher acting outside their course, and a
student reading another student's transcript.

Run: python demo_rbac.py
"""

from __future__ import annotations

from ccl.access import AccessController, Role, RoleService, Workspace
from ccl.access.controller import AccessDenied
from ccl.data import (
    AuditLog, MaterialService, TenantRepository, init_db, make_engine, make_session_factory,
)
from ccl.data.models import Course, SchoolTenant
from ccl.data.tutor_models import TutorMessage, TutorSession
from demo import build_contract


def setup():
    engine = make_engine(); init_db(engine)
    session = make_session_factory(engine)()
    session.add(SchoolTenant(id="school_demo", name="Demo School")); session.flush()
    repo = TenantRepository(session, "school_demo")
    repo.add(Course(id="math_demo", name="Math 51", subject="mathematics"))
    repo.add(Course(id="physics_demo", name="Physics", subject="physics"))
    repo.flush()
    audit = AuditLog(repo)
    MaterialService(repo, audit).ingest(
        material_id="material_notes_03", course_id="math_demo", title="Unit 3", kind="pdf",
        anchored_chunks=[("p12", "page", "The course vector method: express as a linear combination.")],
        actor_id="admin1",
    )
    # Two students' tutor sessions, for the transcript checks.
    for sid, stu in [("sess_s1", "stu_1"), ("sess_s2", "stu_2")]:
        repo.add(TutorSession(id=sid, course_id="math_demo", student_id=stu,
                              mode="practice", contract_version_id="cc_math51_unit3_v1"))
        repo.add(TutorMessage(id=f"m_{sid}", session_id=sid, contract_version_id="cc_math51_unit3_v1",
                              student_message="help", response_text="a hint", hint_level=2, outcome="answered"))
    repo.flush()
    return repo, audit


def line(ok: bool, msg: str) -> None:
    print(f"  {'ALLOW ' if ok else 'DENY  '} {msg}")


def main() -> None:
    repo, audit = setup()
    roles = RoleService(repo, audit)
    ac = AccessController(audit)

    # Admin sets up roles.
    roles.grant("admin1", Role.ADMIN, None, actor_id="system")
    admin = Workspace(roles.load_principal("admin1"), repo, audit, ac)
    admin.grant_role("teach_math", Role.TEACHER, "math_demo")
    admin.grant_role("teach_phys", Role.TEACHER, "physics_demo")
    admin.grant_role("stu_1", Role.STUDENT, "math_demo")

    math_teacher = Workspace(roles.load_principal("teach_math"), repo, audit, ac)
    phys_teacher = Workspace(roles.load_principal("teach_phys"), repo, audit, ac)
    student = Workspace(roles.load_principal("stu_1"), repo, audit, ac)

    print("Math teacher publishes the math contract (in scope):")
    math_teacher.author_contract(build_contract())
    math_teacher.validate_contract("cc_math51_unit3_v1", "math_demo")
    math_teacher.approve_contract("cc_math51_unit3_v1", "math_demo")
    math_teacher.publish_contract("cc_math51_unit3_v1", "math_demo")
    line(True, "math teacher published cc_math51_unit3_v1")

    print("\nLeast-privilege refusals (each is audited):")
    refusals = [
        ("student authors a contract", lambda: student.author_contract(build_contract())),
        ("physics teacher publishes the MATH contract",
         lambda: phys_teacher.publish_contract("cc_math51_unit3_v1", "math_demo")),
        ("student reads another student's transcript",
         lambda: student.access_transcript("sess_s2")),
        ("student grants themselves a teacher role",
         lambda: student.grant_role("stu_1", Role.TEACHER, "math_demo")),
    ]
    for label, fn in refusals:
        try:
            fn()
            line(True, label + " (UNEXPECTED)")
        except AccessDenied:
            line(False, label)

    print("\nAllowed, privacy-sensitive paths:")
    own = student.access_transcript("sess_s1")
    line(True, f"student reads their OWN transcript ({len(own)} msg, not a privacy exception)")
    msgs = math_teacher.access_transcript("sess_s1", reason="student asked for help",
                                          justification="student_escalation")
    line(True, f"teacher reads student transcript with a documented reason ({len(msgs)} msg, audited)")

    print("\nAudit trail (sensitive actions):")
    for e in audit.events():
        if e.action in ("access.denied", "role.grant", "contract.publish",
                        "privacy.raw_transcript_access"):
            print(f"  {e.action:28s} {e.target_type}:{e.target_id}  [{e.detail}]")


if __name__ == "__main__":
    main()
