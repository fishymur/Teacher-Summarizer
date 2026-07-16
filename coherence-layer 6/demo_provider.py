"""End-to-end demo of Milestone 4c: provider registry + runtime governance gate.

Shows an admin registering models, the registry refusing to approve a provider
that trains on data, and the tutor runtime failing closed until an approved,
training-disabled model is in place.

Run: python demo_provider.py
"""

from __future__ import annotations

import asyncio

from ccl.data import (
    AuditLog, ContractService, MaterialService, TenantRepository,
    init_db, make_engine, make_session_factory,
)
from ccl.data.models import Course, SchoolTenant
from ccl.governance import ApprovalRefused, ProviderNotApproved, ProviderRegistry
from ccl.providers import RuleAwareStubProvider
from ccl.providers.base import ProviderCapabilities, ProviderDataPolicy
from ccl.tutor.orchestrator import SessionContext, TutorOrchestrator
from demo import build_contract


class TrainingProvider:
    async def generate(self, r): raise NotImplementedError
    async def embed(self, t): return []
    def data_policy(self):
        return ProviderDataPolicy(provider="risky", region="us", training_disabled=False, retention_days=30)
    def capabilities(self):
        return ProviderCapabilities(model_id="risky-1", context_tokens=1000,
                                    supports_tools=False, supports_structured_output=True)


def setup():
    engine = make_engine(); init_db(engine)
    s = make_session_factory(engine)()
    s.add(SchoolTenant(id="school_demo", name="Demo")); s.flush()
    repo = TenantRepository(s, "school_demo")
    repo.add(Course(id="math_demo", name="Math 51", subject="mathematics")); repo.flush()
    audit = AuditLog(repo)
    MaterialService(repo, audit).ingest(
        material_id="material_notes_03", course_id="math_demo", title="Unit 3", kind="pdf",
        anchored_chunks=[("p12", "page", "The course vector method: express as a linear combination.")],
        actor_id="admin1")
    cs = ContractService(repo, audit)
    cs.create_draft(build_contract()); cs.validate("cc_math51_unit3_v1")
    cs.approve("cc_math51_unit3_v1", approved_by="t1"); cs.publish("cc_math51_unit3_v1", actor_id="t1")
    return repo, audit, cs.get_published("math_demo")


def main():
    repo, audit, contract = setup()
    reg = ProviderRegistry(repo, audit)

    print("Admin registers a data-training provider and tries to approve it:")
    bad = reg.register(TrainingProvider(), approved_uses=["tutoring"], actor_id="admin1")
    try:
        reg.approve(bad.id, actor_id="admin1")
        print("  approved (UNEXPECTED)")
    except ApprovalRefused as e:
        print(f"  REFUSED: {e}")

    orch = TutorOrchestrator(repo, audit, RuleAwareStubProvider(), registry=reg)
    ctx = SessionContext(tenant_id="school_demo", course_id="math_demo", student_id="stu_1",
                         mode="practice", concept_ids=["concept_vectors"], current_unit="unit_3")

    print("\nTutor runtime with an UNAPPROVED model:")
    try:
        asyncio.run(orch.respond(contract, ctx, "help me"))
        print("  answered (UNEXPECTED)")
    except ProviderNotApproved as e:
        print(f"  FAIL CLOSED: {e}")

    print("\nAdmin registers + approves a training-disabled model (with a Gate-3 eval id):")
    good = reg.register(RuleAwareStubProvider(), approved_uses=["tutoring"], actor_id="admin1")
    reg.approve(good.id, actor_id="admin1", eval_run_id="run_gate3_ok")
    turn = asyncio.run(orch.respond(contract, ctx, "help me"))
    print(f"  tutor now responds: outcome={turn.outcome}, verifier={'PASS' if turn.verifier.passed else 'FAIL'}")

    print("\nProvider audit trail:")
    for e in audit.events():
        if e.action.startswith("provider."):
            print(f"  {e.action:20s} {e.target_id}  [{e.detail}]")


if __name__ == "__main__":
    main()
