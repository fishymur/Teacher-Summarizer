"""End-to-end demo of Milestone 4b: retention, erasure, and export.

Shows the privacy separation a school reviewer cares about:
- raw student content is redacted on a short clock, while the de-identified
  aggregate insight and the audit log survive;
- a right-to-erasure request removes one student's learning data but not
  class-level patterns or another student's data;
- retention/purge is admin-only at the Workspace boundary.

Run: python demo_privacy.py
"""

from __future__ import annotations

import datetime as dt

from ccl.access import AccessDenied, Principal, Role, RoleGrant, Workspace
from ccl.data import AuditLog, TenantRepository, init_db, make_engine, make_session_factory
from ccl.data.insight_models import TeacherInsight
from ccl.data.models import Course, SchoolTenant
from ccl.data.tutor_models import InteractionEvent, TutorMessage, TutorSession
from ccl.privacy import REDACTED, DeletionService, ExportService, RetentionService

NOW = dt.datetime.now(dt.timezone.utc)


def age(d):
    return NOW - dt.timedelta(days=d)


def setup():
    engine = make_engine(); init_db(engine)
    s = make_session_factory(engine)()
    s.add(SchoolTenant(id="school_demo", name="Demo")); s.flush()
    repo = TenantRepository(s, "school_demo")
    repo.add(Course(id="math_demo", name="Math 51", subject="mathematics")); repo.flush()
    audit = AuditLog(repo)

    for mid, stu, days, sess in [
        ("m_ancient", "stu_1", 400, "sess_1"),
        ("m_old", "stu_1", 200, "sess_1"),
        ("m_new", "stu_1", 5, "sess_1"),
        ("m_other", "stu_2", 5, "sess_2"),
    ]:
        if repo.get(TutorSession, sess) is None:
            repo.add(TutorSession(id=sess, course_id="math_demo", student_id=stu,
                                  mode="practice", contract_version_id="cc_v1"))
        repo.add(TutorMessage(id=mid, session_id=sess, contract_version_id="cc_v1",
                              student_message="please help", response_text="a hint",
                              hint_level=2, outcome="answered", created_at=age(days)))
    repo.add(InteractionEvent(id="e1", course_id="math_demo", session_id="sess_1",
                              type="tutor_help_requested", concept_id="concept_vectors"))
    repo.add(TeacherInsight(id="ins_1", course_id="math_demo", window_start="w0", window_end="w1",
                            type="misconception_cluster", concept_id="concept_vectors",
                            summary="students confuse magnitude with sign", confidence=0.8,
                            sample_size=6, created_at=age(10)))
    repo.flush()
    return repo, audit


def state(repo, label):
    def raw(mid):
        m = repo.get(TutorMessage, mid)
        if m is None:
            return "DELETED"
        return "REDACTED" if m.student_message == REDACTED else "intact"
    ins = "present" if repo.get(TeacherInsight, "ins_1") else "gone"
    print(f"  {label:24s} m_ancient(400d)={raw('m_ancient')}  m_old(200d)={raw('m_old')}  "
          f"m_new(5d)={raw('m_new')}  aggregate_insight={ins}")


def main():
    repo, audit = setup()
    admin = Workspace(Principal("admin1", "school_demo", (RoleGrant(Role.ADMIN, None),)), repo, audit)

    print("Retention purge (raw=180d, trace=365d, insights=730d):")
    state(repo, "before purge")
    report = admin.run_retention_purge(now=NOW)
    state(repo, "after purge")
    print(f"  report: {report.actions}")

    print("\nRight-to-erasure for stu_1:")
    DeletionService(repo, audit).request_and_execute(subject_user_id="stu_1", requested_by="admin1")
    gone = repo.get(TutorMessage, "m_new") is None and repo.get(TutorSession, "sess_1") is None
    other = repo.get(TutorMessage, "m_other") is not None
    agg = repo.get(TeacherInsight, "ins_1") is not None
    print(f"  stu_1 learning data erased: {gone}")
    print(f"  stu_2 data untouched:       {other}")
    print(f"  aggregate insight retained: {agg}")

    print("\nExport (stu_2 self-service):")
    student = Workspace(Principal("stu_2", "school_demo", (RoleGrant(Role.STUDENT, "math_demo"),)), repo, audit)
    bundle = student.export_user_data("stu_2")
    print(f"  exported {len(bundle['messages'])} message(s) for stu_2")
    try:
        student.run_retention_purge(now=NOW)
    except AccessDenied:
        print("  student attempt to run purge: DENIED (admin-only)")

    print("\nAudit trail (privacy actions):")
    for e in audit.events():
        if e.action.startswith("privacy."):
            print(f"  {e.action:30s} {e.target_type}:{e.target_id}")


if __name__ == "__main__":
    main()
