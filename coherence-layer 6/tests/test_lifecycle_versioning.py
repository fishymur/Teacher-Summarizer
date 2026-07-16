"""Acceptance: contract lifecycle, versioning, and publish behaviour (section 6).

- only a published contract governs;
- publishing supersedes the prior published version for the course;
- an invalid contract cannot be approved;
- a change forks a new version rather than mutating the published one;
- illegal transitions are rejected.
"""

import pytest

from ccl.contracts.lifecycle import IllegalTransition, assert_transition, is_governing
from ccl.contracts.schema import ContractStatus
from ccl.data import ContractPublishError
from ccl.data.models import CurriculumContractRow
from tests.conftest import make_valid_contract


def _publish_v1(contract_service):
    contract_service.create_draft(make_valid_contract())
    result = contract_service.validate("cc_math51_unit3_v1")
    assert result.is_valid, sorted(c.value for c in result.codes)
    contract_service.approve("cc_math51_unit3_v1", approved_by="user_teacher_123")
    contract_service.publish("cc_math51_unit3_v1", actor_id="user_teacher_123")


def test_only_published_governs():
    assert is_governing(ContractStatus.PUBLISHED) is True
    for status in ContractStatus:
        if status != ContractStatus.PUBLISHED:
            assert is_governing(status) is False


def test_full_publish_flow(repo, contract_service, seeded_material):
    _publish_v1(contract_service)
    row = repo.get(CurriculumContractRow, "cc_math51_unit3_v1")
    assert row.status == ContractStatus.PUBLISHED.value
    published = contract_service.get_published("math_demo")
    assert published is not None
    assert published.version == 1


def test_cannot_approve_invalid_contract(contract_service, seeded_material):
    # Missing golden set -> invalid -> approval refused.
    invalid = make_valid_contract(golden_case_ids=["only_one"])
    contract_service.create_draft(invalid)
    with pytest.raises(ContractPublishError):
        contract_service.approve("cc_math51_unit3_v1", approved_by="user_teacher_123")


def test_new_version_supersedes_prior(repo, contract_service, seeded_material):
    _publish_v1(contract_service)

    # Fork v2, publish it, and confirm v1 is superseded and v2 governs.
    v2 = contract_service.create_new_version("cc_math51_unit3_v1")
    contract_service.validate(v2.id)
    contract_service.approve(v2.id, approved_by="user_teacher_123")
    contract_service.publish(v2.id, actor_id="user_teacher_123")

    v1 = repo.get(CurriculumContractRow, "cc_math51_unit3_v1")
    assert v1.status == ContractStatus.SUPERSEDED.value
    published = contract_service.get_published("math_demo")
    assert published.version == 2


def test_illegal_transition_rejected():
    # Cannot jump straight from draft to published.
    with pytest.raises(IllegalTransition):
        assert_transition(ContractStatus.DRAFT, ContractStatus.PUBLISHED)
    # An archived contract is terminal.
    with pytest.raises(IllegalTransition):
        assert_transition(ContractStatus.ARCHIVED, ContractStatus.DRAFT)
