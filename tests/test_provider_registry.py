"""Acceptance: provider registry and runtime governance gate (sections 15, 17).

Proves the registry records a provider's declared data policy, refuses to
approve a provider that trains on data, gates runtime usage (fail closed), and
locks registry management behind admin rights.
"""

from __future__ import annotations

import asyncio

import pytest

from ccl.access import AccessDenied, Principal, Role, RoleGrant, Workspace
from ccl.data import ContractService
from ccl.governance import ApprovalRefused, ProviderNotApproved, ProviderRegistry
from ccl.providers import RuleAwareStubProvider
from ccl.providers.base import ProviderCapabilities, ProviderDataPolicy
from ccl.tutor.orchestrator import SessionContext, TutorOrchestrator
from tests.conftest import make_valid_contract


class TrainingProvider:
    """A provider that reserves the right to train on data — must be refused."""

    async def generate(self, request):  # pragma: no cover - not called
        raise NotImplementedError

    async def embed(self, texts):  # pragma: no cover
        return []

    def data_policy(self):
        return ProviderDataPolicy(provider="risky", region="us", training_disabled=False, retention_days=30)

    def capabilities(self):
        return ProviderCapabilities(model_id="risky-1", context_tokens=1000,
                                    supports_tools=False, supports_structured_output=True)


def test_register_reads_declared_policy(repo, audit):
    reg = ProviderRegistry(repo, audit)
    rec = reg.register(RuleAwareStubProvider(), approved_uses=["tutoring"], actor_id="admin1")
    assert rec.provider == "stub"
    assert rec.model_id == "stub-compliant-1"
    assert rec.training_disabled is True
    assert rec.status == "registered"
    assert any(e.action == "provider.register" for e in audit.events())


def test_approve_refuses_when_training_not_disabled(repo, audit):
    reg = ProviderRegistry(repo, audit)
    rec = reg.register(TrainingProvider(), approved_uses=["tutoring"], actor_id="admin1")
    with pytest.raises(ApprovalRefused):
        reg.approve(rec.id, actor_id="admin1")


def test_approve_records_eval_and_audits(repo, audit):
    reg = ProviderRegistry(repo, audit)
    rec = reg.register(RuleAwareStubProvider(), approved_uses=["tutoring"], actor_id="admin1")
    reg.approve(rec.id, actor_id="admin1", eval_run_id="run_gate3_ok")
    assert reg._repo.get(type(rec), rec.id).status == "approved"
    assert reg._repo.get(type(rec), rec.id).eval_run_id == "run_gate3_ok"
    assert any(e.action == "provider.approve" for e in audit.events())


def test_ensure_usable_states(repo, audit):
    reg = ProviderRegistry(repo, audit)
    with pytest.raises(ProviderNotApproved):
        reg.ensure_usable("stub-compliant-1")  # not registered
    rec = reg.register(RuleAwareStubProvider(), approved_uses=["tutoring"], actor_id="admin1")
    with pytest.raises(ProviderNotApproved):
        reg.ensure_usable("stub-compliant-1")  # registered but not approved
    reg.approve(rec.id, actor_id="admin1")
    assert reg.ensure_usable("stub-compliant-1").status == "approved"
    reg.revoke(rec.id, actor_id="admin1")
    with pytest.raises(ProviderNotApproved):
        reg.ensure_usable("stub-compliant-1")  # revoked


def _publish(contract_service):
    contract_service.create_draft(make_valid_contract())
    contract_service.validate("cc_math51_unit3_v1")
    contract_service.approve("cc_math51_unit3_v1", approved_by="t1")
    contract_service.publish("cc_math51_unit3_v1", actor_id="t1")
    return contract_service.get_published("math_demo")


def test_orchestrator_fails_closed_until_approved(repo, audit, seeded_material):
    contract = _publish(ContractService(repo, audit))
    reg = ProviderRegistry(repo, audit)
    orch = TutorOrchestrator(repo, audit, RuleAwareStubProvider(), registry=reg)
    ctx = SessionContext(tenant_id="school_demo", course_id="math_demo", student_id="stu_1",
                         mode="practice", concept_ids=["concept_vectors"], current_unit="unit_3")

    # Unapproved model -> the runtime refuses to call it.
    with pytest.raises(ProviderNotApproved):
        asyncio.run(orch.respond(contract, ctx, "help me"))

    rec = reg.register(RuleAwareStubProvider(), approved_uses=["tutoring"], actor_id="admin1")
    reg.approve(rec.id, actor_id="admin1", eval_run_id="run_gate3_ok")

    turn = asyncio.run(orch.respond(contract, ctx, "help me"))
    assert turn.outcome in ("answered", "revised", "fallback")


def test_workspace_provider_management_is_admin_only(repo, audit):
    student = Workspace(Principal("s1", "school_demo", (RoleGrant(Role.STUDENT, "math_demo"),)), repo, audit)
    with pytest.raises(AccessDenied):
        student.register_provider(RuleAwareStubProvider(), approved_uses=["tutoring"])

    admin = Workspace(Principal("a1", "school_demo", (RoleGrant(Role.ADMIN, None),)), repo, audit)
    rec = admin.register_provider(RuleAwareStubProvider(), approved_uses=["tutoring"])
    admin.approve_provider(rec.id, eval_run_id="run_gate3_ok")
    assert len(admin.list_providers()) == 1
