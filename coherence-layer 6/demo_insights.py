"""End-to-end demo of Milestone 3: Teacher Insights (headless, offline).

Runs the tutor for a cohort of students to generate real interaction events,
builds the weekly insight brief, shows the observed/inferred/recommended
structure (with no student identity), then exercises the review + correction
loop and prints signal-quality metrics.

Run: python demo_insights.py
"""

from __future__ import annotations

import asyncio
import datetime as dt

from ccl.data import (
    AuditLog, ContractService, MaterialService, TenantRepository,
    init_db, make_engine, make_session_factory,
)
from ccl.data.models import Course, SchoolTenant
from ccl.data.insight_models import TeacherInsight
from ccl.insights import (
    CorrectionKind, CorrectionService, InsightService, ReviewAction,
    WeeklyBriefBuilder, signal_quality,
)
from ccl.providers import RuleAwareStubProvider
from ccl.tutor.orchestrator import SessionContext, TutorOrchestrator

from demo import build_contract


def setup():
    engine = make_engine(); init_db(engine)
    session = make_session_factory(engine)()
    session.add(SchoolTenant(id="school_demo", name="Demo School")); session.flush()
    repo = TenantRepository(session, "school_demo")
    repo.add(Course(id="math_demo", name="Math 51", subject="mathematics")); repo.flush()
    audit = AuditLog(repo)
    MaterialService(repo, audit).ingest(
        material_id="material_notes_03", course_id="math_demo",
        title="Unit 3 notes", kind="pdf",
        anchored_chunks=[("p12", "page", "The course vector method: express as a linear combination."),
                         ("p13", "page", "Worked example."), ("p14", "page", "Practice problems.")],
        actor_id="t1",
    )
    contracts = ContractService(repo, audit)
    contracts.create_draft(build_contract())
    contracts.validate("cc_math51_unit3_v1")
    contracts.approve("cc_math51_unit3_v1", approved_by="t1")
    contracts.publish("cc_math51_unit3_v1", actor_id="t1")
    return repo, audit, contracts, contracts.get_published("math_demo")


async def main() -> None:
    repo, audit, contracts, contract = setup()
    orch = TutorOrchestrator(repo, audit, RuleAwareStubProvider())

    # A cohort of 6 students each struggles on vectors (asks for the answer with
    # an attempt, hitting the practice hint ceiling).
    for i in range(6):
        ctx = SessionContext(
            tenant_id="school_demo", course_id="math_demo", student_id=f"stu_{i}",
            mode="practice", concept_ids=["concept_vectors"], current_unit="unit_3",
        )
        await orch.respond(contract, ctx, "Just give me the final answer.",
                           student_attempt="I added the components but I'm stuck on direction.")

    now = dt.datetime.now(dt.timezone.utc)
    window = (now - dt.timedelta(days=1), now + dt.timedelta(days=1))
    brief = WeeklyBriefBuilder(repo, audit).build(contract, "math_demo", *window)

    print("=" * 72)
    print(f"WEEKLY INSIGHT BRIEF  (course=math_demo)")
    print(f"review time estimate: {brief.review_time_estimate_minutes} min | "
          f"out-of-scope questions: {brief.out_of_scope_count}")
    for view in brief.misconception_clusters + brief.full_solution_pressure + brief.prerequisite_gaps:
        inf = view.inferred
        print(f"\n[{view.status.value}]  {inf.type}  ->  {inf.concept_name}")
        print(f"  OBSERVED:")
        for o in view.observed:
            print(f"    - {o.metric}: {o.value}/{o.denominator} (scope={o.scope})")
        print(f"  INFERRED: {inf.summary}")
        print(f"    confidence={inf.confidence} | cohort={inf.sample_size} | "
              f"counter-evidence={inf.counterevidence_count} students")
        print(f"    evidence events: {len(inf.supporting_event_ids)} | sources: {inf.source_refs}")
        print(f"  RECOMMENDED: {view.recommended.text}")
        print(f"    controls: {view.recommended.controls}")

    # Confirm no student identity leaked into the brief.
    assert "stu_" not in brief.model_dump_json()
    print("\n(No individual student appears anywhere in this brief.)")

    # Teacher reviews the top insight and files a correction that forks a DRAFT
    # contract change — the published contract is never edited.
    top = repo.list(TeacherInsight, course_id="math_demo")[0]
    InsightService(repo, audit).review(top.id, ReviewAction.CONFIRM, actor_id="t1")
    CorrectionService(repo, audit, contracts).submit(
        course_id="math_demo", target_type="insight", target_id=top.id,
        kind=CorrectionKind.TOO_LITTLE_HELP, created_by="t1",
        resulting_action="draft_contract_change", base_contract_id="cc_math51_unit3_v1",
    )
    print(f"\nPublished contract after correction: v"
          f"{contracts.get_published('math_demo').version} (unchanged); "
          f"a new draft v2 awaits teacher edits.")

    print("\nSIGNAL QUALITY:")
    for k, v in signal_quality(repo, audit, "math_demo").items():
        print(f"  {k:20s} {v}")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
