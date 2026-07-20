"""Acceptance: the deterministic policy engine (section 14).

This is the executable-pedagogy core the tutor runtime will consume. The
canonical scenario is the Math 51 case: a Practice-mode request touching the
vectors concept while the class is still in unit 3 must forbid the cross
product, require the course method, cap help at the practice ceiling, and carry
a sequence-boundary explanation.
"""

import pytest

from ccl.contracts.schema import ContractStatus
from ccl.policy import (
    ContractNotGoverning,
    PolicyEngine,
    RequestContext,
)
from tests.conftest import make_valid_contract


def _published():
    return make_valid_contract().model_copy(update={"status": ContractStatus.PUBLISHED})


def test_decision_requires_published_contract():
    engine = PolicyEngine()
    draft = make_valid_contract()  # status defaults to draft
    with pytest.raises(ContractNotGoverning):
        engine.decide(draft, RequestContext(mode="practice", concept_ids=["concept_vectors"]))


def test_practice_mode_cross_product_scenario():
    engine = PolicyEngine()
    decision = engine.decide(
        _published(),
        RequestContext(
            mode="practice",
            concept_ids=["concept_vectors"],
            current_unit="unit_3",
        ),
    )
    assert decision.max_hint_level == 5
    assert "cross_product" in decision.forbidden_method_ids
    assert "course_vector_method" in decision.required_method_ids
    assert decision.full_solution_allowed is False
    assert decision.external_sources_allowed is False
    assert decision.allowed_source_ids == ["material_notes_03", "material_slides_03"]
    assert "rule_cross_product_not_yet" in decision.explanations
    assert "rule_practice_hint_ceiling" in decision.explanations
    # A student-facing boundary message is attached to the not-yet rule.
    assert "outside the current course sequence" in \
        decision.student_explanations["rule_cross_product_not_yet"]


def test_assessment_mode_caps_help():
    engine = PolicyEngine()
    decision = engine.decide(
        _published(),
        RequestContext(mode="assessment", concept_ids=["concept_vectors"], current_unit="unit_3"),
    )
    assert decision.max_hint_level == 2
    assert decision.full_solution_allowed is False


def test_method_unlocks_after_its_unit():
    engine = PolicyEngine()
    # Once the class reaches unit 6, the cross product is no longer restricted.
    decision = engine.decide(
        _published(),
        RequestContext(mode="learn", concept_ids=["concept_vectors"], current_unit="unit_7"),
    )
    assert "cross_product" not in decision.forbidden_method_ids
    # Learn ceiling is 6, so a full solution may be released (attempt gating is
    # applied later by the tutor runtime).
    assert decision.max_hint_level == 6
    assert decision.full_solution_allowed is True


def test_unknown_current_unit_fails_closed():
    engine = PolicyEngine()
    # No current unit supplied: the restricted method must remain forbidden.
    decision = engine.decide(
        _published(),
        RequestContext(mode="practice", concept_ids=["concept_vectors"], current_unit=None),
    )
    assert "cross_product" in decision.forbidden_method_ids


def test_decision_is_deterministic():
    engine = PolicyEngine()
    ctx = RequestContext(mode="practice", concept_ids=["concept_vectors"], current_unit="unit_3")
    d1 = engine.decide(_published(), ctx)
    d2 = engine.decide(_published(), ctx)
    assert d1 == d2
