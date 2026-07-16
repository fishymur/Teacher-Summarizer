"""Acceptance: audit events are recorded and append-only (section 11).

The milestone must log material import, contract approval, and contract publish.
Audit rows cannot be updated or deleted.
"""

import pytest

from ccl.data.models import AuditEvent, ImmutableRowError
from tests.conftest import make_valid_contract


def test_sensitive_actions_are_audited(repo, audit, contract_service, seeded_material):
    contract_service.create_draft(make_valid_contract())
    contract_service.validate("cc_math51_unit3_v1")
    contract_service.approve("cc_math51_unit3_v1", approved_by="user_teacher_123")
    contract_service.publish("cc_math51_unit3_v1", actor_id="user_teacher_123")

    actions = {e.action for e in audit.events()}
    assert {"material.import", "contract.approve", "contract.publish"} <= actions


def test_audit_events_cannot_be_updated(session, repo, audit):
    evt = audit.record(action="test.action", target_type="X", target_id="x1")
    evt.detail = "tampered"
    with pytest.raises(ImmutableRowError):
        session.flush()


def test_audit_events_cannot_be_deleted(session, repo, audit):
    evt = audit.record(action="test.action", target_type="X", target_id="x2")
    session.delete(evt)
    with pytest.raises(ImmutableRowError):
        session.flush()
