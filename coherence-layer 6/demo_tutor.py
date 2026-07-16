"""End-to-end demo of Milestone 2: the Tutor Runtime (headless, offline).

Walks the student flow from section 19 using the deterministic compliant
provider, so it runs with no API key and no network:

    1. ask for the final answer with no attempt  -> diagnostic prompt, not the answer
    2. submit an attempt                          -> cited hint using the course method
    3. ask for the restricted cross product       -> sequence-aware boundary decline

Every turn is verified against the published contract before it is shown. The
turn also carries the citation anchor (which page/slide it points to), the fired
policy rules, and the verifier verdict.

Run: python demo_tutor.py
"""

from __future__ import annotations

import asyncio

from ccl.data import (
    AuditLog, ContractService, MaterialService, TenantRepository,
    init_db, make_engine, make_session_factory,
)
from ccl.data.models import Course, SchoolTenant
from ccl.providers import RuleAwareStubProvider
from ccl.tutor.orchestrator import SessionContext, TutorOrchestrator

# Reuse the Milestone-1 demo's contract builder.
from demo import build_contract


def setup():
    engine = make_engine()
    init_db(engine)
    session = make_session_factory(engine)()
    session.add(SchoolTenant(id="school_demo", name="Demo School"))
    session.flush()
    repo = TenantRepository(session, "school_demo")
    repo.add(Course(id="math_demo", name="Math 51", subject="mathematics"))
    repo.flush()
    audit = AuditLog(repo)

    MaterialService(repo, audit).ingest(
        material_id="material_notes_03", course_id="math_demo",
        title="Unit 3 notes", kind="pdf",
        anchored_chunks=[
            ("p12", "page", "The course vector method: express as a linear combination."),
            ("p13", "page", "Worked example using the course method."),
            ("p14", "page", "Practice problems."),
        ],
        actor_id="user_teacher_123",
    )

    contracts = ContractService(repo, audit)
    contracts.create_draft(build_contract())
    contracts.validate("cc_math51_unit3_v1")
    contracts.approve("cc_math51_unit3_v1", approved_by="user_teacher_123")
    contracts.publish("cc_math51_unit3_v1", actor_id="user_teacher_123")
    published = contracts.get_published("math_demo")

    orch = TutorOrchestrator(repo, audit, RuleAwareStubProvider())
    return orch, published


def show(label: str, turn) -> None:
    print(f"\n{'='*70}\n{label}\n{'-'*70}")
    print(f"tutor> {turn.response_text}")
    cites = ", ".join(f"{c['source_id']}:{c['anchor']}" for c in turn.citations) or "(none)"
    print(f"       [hint level {turn.hint_level} | outcome={turn.outcome} | "
          f"verifier={'PASS' if turn.verifier.passed else 'FAIL'}]")
    print(f"       [citation opens: {cites}]")
    print(f"       [policy rules fired: {turn.policy.explanations}]")


async def main() -> None:
    orch, contract = setup()
    ctx = SessionContext(
        tenant_id="school_demo", course_id="math_demo", student_id="stu_1",
        mode="practice", concept_ids=["concept_vectors"], current_unit="unit_3",
    )

    t1 = await orch.respond(contract, ctx, "Just give me the final answer to problem 4.")
    show("1) Student asks for the answer with NO attempt (Practice mode)", t1)

    ctx.session_id = t1.session_id  # continue the same session
    t2 = await orch.respond(
        contract, ctx,
        "Here's the problem about combining two vectors.",
        student_attempt="I tried adding the components but I'm not sure about the direction.",
    )
    show("2) Student submits an attempt", t2)

    t3 = await orch.respond(contract, ctx, "Can I just use the cross product here?")
    show("3) Student asks for the restricted cross product", t3)

    print(f"\n{'='*70}\nAll three turns were verified against the published contract "
          f"before display.\n")


if __name__ == "__main__":
    asyncio.run(main())
