"""Acceptance: the contract publish gate enforces all nine rules in section 6.

Each rule has a negative case that must produce its specific violation code,
plus a positive case where a well-formed contract passes cleanly.
"""

import pytest

from ccl.contracts.schema import (
    Analytics,
    Methods,
    NotYetIntroducedMethod,
    Pedagogy,
    PreferredMethod,
    ProhibitedMethod,
)
from ccl.validation import (
    InMemoryAnchorResolver,
    ValidationCode,
    validate_for_publish,
)
from tests.conftest import make_valid_contract


@pytest.fixture()
def resolver():
    # Seed the exact source ref used by the valid contract.
    return InMemoryAnchorResolver({"material_notes_03": {"p12-p14"}})


def test_valid_contract_passes(resolver):
    result = validate_for_publish(make_valid_contract(), resolver)
    assert result.is_valid, sorted(c.value for c in result.codes)


def test_rule1_method_without_stable_id(resolver):
    c = make_valid_contract(
        methods=Methods(
            preferred=[
                PreferredMethod(id="", name="unnamed", applies_to=["concept_vectors"],
                                source_refs=["material_notes_03:p12-p14"])
            ],
            not_yet_introduced=[
                NotYetIntroducedMethod(id="cross_product", name="cross product",
                                       until_unit="unit_6", applies_to=["concept_vectors"],
                                       response_rule="outside sequence")
            ],
        )
    )
    result = validate_for_publish(c, resolver)
    assert ValidationCode.METHOD_MISSING_STABLE_ID in result.codes


def test_rule2_unresolved_source_ref(resolver):
    c = make_valid_contract(
        methods=Methods(
            preferred=[
                PreferredMethod(id="course_vector_method", name="m",
                                applies_to=["concept_vectors"],
                                source_refs=["material_notes_03:p999"])  # anchor not seeded
            ],
            not_yet_introduced=[
                NotYetIntroducedMethod(id="cross_product", name="cross product",
                                       until_unit="unit_6", applies_to=["concept_vectors"],
                                       response_rule="outside sequence")
            ],
        )
    )
    result = validate_for_publish(c, resolver)
    assert ValidationCode.SOURCE_REF_UNRESOLVED in result.codes


def test_rule3_preferred_and_prohibited(resolver):
    c = make_valid_contract(
        methods=Methods(
            preferred=[
                PreferredMethod(id="course_vector_method", name="m",
                                applies_to=["concept_vectors"],
                                source_refs=["material_notes_03:p12-p14"])
            ],
            prohibited=[ProhibitedMethod(id="course_vector_method", name="m")],
            not_yet_introduced=[
                NotYetIntroducedMethod(id="cross_product", name="cross product",
                                       until_unit="unit_6", applies_to=["concept_vectors"],
                                       response_rule="outside sequence")
            ],
        )
    )
    result = validate_for_publish(c, resolver)
    assert ValidationCode.METHOD_PREFERRED_AND_PROHIBITED in result.codes


def test_rule4_missing_mode_ceiling(resolver):
    c = make_valid_contract(
        pedagogy=Pedagogy(
            maximum_hint_level_by_mode={"learn": 6, "practice": 5, "review": 6},  # no assessment
            full_solution_policy="x",
        )
    )
    result = validate_for_publish(c, resolver)
    assert ValidationCode.MODE_CEILING_MISSING in result.codes


def test_rule4_ceiling_out_of_range(resolver):
    c = make_valid_contract(
        pedagogy=Pedagogy(
            maximum_hint_level_by_mode={"learn": 6, "practice": 5, "review": 6, "assessment": 9},
            full_solution_policy="x",
        )
    )
    result = validate_for_publish(c, resolver)
    assert ValidationCode.MODE_CEILING_OUT_OF_RANGE in result.codes


def test_rule5_concept_graph_not_approved(resolver):
    c = make_valid_contract(concept_graph_approved=False)
    result = validate_for_publish(c, resolver)
    assert ValidationCode.CONCEPT_GRAPH_NOT_APPROVED in result.codes


def test_rule6_insufficient_golden_cases(resolver):
    c = make_valid_contract(golden_case_ids=[f"eval_{i}" for i in range(5)])
    result = validate_for_publish(c, resolver)
    assert ValidationCode.INSUFFICIENT_GOLDEN_CASES in result.codes


def test_rule7_not_yet_introduced_missing_boundary(resolver):
    c = make_valid_contract(
        methods=Methods(
            preferred=[
                PreferredMethod(id="course_vector_method", name="m",
                                applies_to=["concept_vectors"],
                                source_refs=["material_notes_03:p12-p14"])
            ],
            not_yet_introduced=[
                NotYetIntroducedMethod(id="cross_product", name="cross product",
                                       until_unit="unit_6", applies_to=["concept_vectors"],
                                       response_rule="   ")  # blank boundary note
            ],
        )
    )
    result = validate_for_publish(c, resolver)
    assert ValidationCode.NOT_YET_INTRODUCED_MISSING_BOUNDARY in result.codes


def test_rule8_privacy_not_configured(resolver):
    c = make_valid_contract(
        analytics=Analytics(retention_days=0, raw_chat_visibility="")
    )
    result = validate_for_publish(c, resolver)
    assert ValidationCode.PRIVACY_NOT_CONFIGURED in result.codes


def test_rule9_contradictory_method_state(resolver):
    c = make_valid_contract(
        methods=Methods(
            preferred=[
                PreferredMethod(id="course_vector_method", name="m",
                                applies_to=["concept_vectors"],
                                source_refs=["material_notes_03:p12-p14"])
            ],
            not_yet_introduced=[
                # same id appears as both preferred and not_yet_introduced
                NotYetIntroducedMethod(id="course_vector_method", name="dup",
                                       until_unit="unit_6", applies_to=["concept_vectors"],
                                       response_rule="outside sequence")
            ],
        )
    )
    result = validate_for_publish(c, resolver)
    assert ValidationCode.CONTRADICTORY_METHOD_STATE in result.codes
