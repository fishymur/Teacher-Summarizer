"""Contract publish-gate validator.

Implements the nine conditions from section 6 that a contract must satisfy
before it can publish. The validator returns a structured result (a list of
coded violations) so that both the API and the acceptance tests can assert on
*which* rule failed rather than on prose.

Source-reference resolution (rule 2) depends on the materials store, so the
validator takes an ``AnchorResolver`` rather than reaching into the database
directly. This keeps the rule engine pure and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ..contracts.schema import (
    MAX_HINT_LEVEL,
    MIN_HINT_LEVEL,
    CurriculumContract,
)

REQUIRED_MODES = ("learn", "practice", "review", "assessment")
MIN_GOLDEN_CASES = 20


class ValidationCode(str, Enum):
    METHOD_MISSING_STABLE_ID = "method_missing_stable_id"          # rule 1
    SOURCE_REF_UNRESOLVED = "source_ref_unresolved"                # rule 2
    METHOD_PREFERRED_AND_PROHIBITED = "method_preferred_and_prohibited"  # rule 3
    MODE_CEILING_MISSING = "mode_ceiling_missing"                  # rule 4
    MODE_CEILING_OUT_OF_RANGE = "mode_ceiling_out_of_range"        # rule 4
    CONCEPT_GRAPH_NOT_APPROVED = "concept_graph_not_approved"      # rule 5
    INSUFFICIENT_GOLDEN_CASES = "insufficient_golden_cases"        # rule 6
    NOT_YET_INTRODUCED_MISSING_BOUNDARY = "not_yet_introduced_missing_boundary"  # rule 7
    PRIVACY_NOT_CONFIGURED = "privacy_not_configured"              # rule 8
    CONTRADICTORY_METHOD_STATE = "contradictory_method_state"      # rule 9


@dataclass(frozen=True)
class Violation:
    code: ValidationCode
    detail: str


@dataclass
class ValidationResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.violations

    @property
    def codes(self) -> set[ValidationCode]:
        return {v.code for v in self.violations}

    def add(self, code: ValidationCode, detail: str) -> None:
        self.violations.append(Violation(code, detail))


class AnchorResolver(Protocol):
    """Resolves a contract source reference to an approved material + anchor.

    A reference looks like ``material_notes_03:p12-p14``. It resolves only when
    the material id is in the contract's approved list *and* the anchor exists
    in the stored material version.
    """

    def resolves(self, source_ref: str, approved_material_ids: list[str]) -> bool:
        ...


class InMemoryAnchorResolver:
    """Test/utility resolver seeded with known (material_id, anchor) pairs."""

    def __init__(self, known_anchors: dict[str, set[str]]) -> None:
        # material_id -> set of anchor labels that exist for it
        self._known = known_anchors

    def resolves(self, source_ref: str, approved_material_ids: list[str]) -> bool:
        material_id, _, anchor = source_ref.partition(":")
        if material_id not in approved_material_ids:
            return False
        anchors = self._known.get(material_id)
        if anchors is None:
            return False
        # An empty anchor is not a valid resolution: we require an exact anchor.
        return bool(anchor) and anchor in anchors


def validate_for_publish(
    contract: CurriculumContract,
    resolver: AnchorResolver,
) -> ValidationResult:
    """Run all nine publish-gate rules and return every violation found."""
    result = ValidationResult()

    _rule_1_stable_ids(contract, result)
    _rule_2_source_refs(contract, resolver, result)
    _rule_3_and_9_method_states(contract, result)
    _rule_4_mode_ceilings(contract, result)
    _rule_5_concept_graph(contract, result)
    _rule_6_golden_cases(contract, result)
    _rule_7_not_yet_boundaries(contract, result)
    _rule_8_privacy_config(contract, result)

    return result


def _rule_1_stable_ids(contract: CurriculumContract, result: ValidationResult) -> None:
    m = contract.methods
    for method in [*m.preferred, *m.not_yet_introduced, *m.prohibited]:
        if not getattr(method, "id", "").strip():
            result.add(
                ValidationCode.METHOD_MISSING_STABLE_ID,
                f"method {method!r} has no stable id",
            )


def _rule_2_source_refs(
    contract: CurriculumContract, resolver: AnchorResolver, result: ValidationResult
) -> None:
    approved = contract.source_policy.approved_material_ids
    for method in contract.methods.preferred:
        for ref in method.source_refs:
            if not resolver.resolves(ref, approved):
                result.add(
                    ValidationCode.SOURCE_REF_UNRESOLVED,
                    f"source_ref {ref!r} does not resolve to an approved material and anchor",
                )


def _rule_3_and_9_method_states(
    contract: CurriculumContract, result: ValidationResult
) -> None:
    m = contract.methods
    preferred = {x.id for x in m.preferred}
    allowed = {x.id for x in m.allowed}
    not_yet = {x.id for x in m.not_yet_introduced}
    prohibited = {x.id for x in m.prohibited}

    # rule 3: a method cannot be both preferred and prohibited.
    for mid in preferred & prohibited:
        result.add(
            ValidationCode.METHOD_PREFERRED_AND_PROHIBITED,
            f"method {mid!r} is both preferred and prohibited",
        )

    # rule 9 (contradiction check): a method id may appear in at most one state.
    buckets = {
        "preferred": preferred,
        "allowed": allowed,
        "not_yet_introduced": not_yet,
        "prohibited": prohibited,
    }
    seen: dict[str, str] = {}
    for state, ids in buckets.items():
        for mid in ids:
            if mid in seen and seen[mid] != state:
                # Skip the preferred+prohibited pair already reported by rule 3.
                if {seen[mid], state} == {"preferred", "prohibited"}:
                    continue
                result.add(
                    ValidationCode.CONTRADICTORY_METHOD_STATE,
                    f"method {mid!r} appears in both {seen[mid]!r} and {state!r}",
                )
            else:
                seen[mid] = state


def _rule_4_mode_ceilings(
    contract: CurriculumContract, result: ValidationResult
) -> None:
    ceilings = contract.pedagogy.maximum_hint_level_by_mode
    for mode in REQUIRED_MODES:
        if mode not in ceilings:
            result.add(
                ValidationCode.MODE_CEILING_MISSING,
                f"mode ceiling for {mode!r} is missing",
            )
            continue
        level = ceilings[mode]
        if not (MIN_HINT_LEVEL <= level <= MAX_HINT_LEVEL):
            result.add(
                ValidationCode.MODE_CEILING_OUT_OF_RANGE,
                f"mode ceiling for {mode!r} is {level}, outside 1-6",
            )


def _rule_5_concept_graph(
    contract: CurriculumContract, result: ValidationResult
) -> None:
    if not contract.concept_graph_approved:
        result.add(
            ValidationCode.CONCEPT_GRAPH_NOT_APPROVED,
            "concept/prerequisite graph has not been approved by a teacher",
        )


def _rule_6_golden_cases(
    contract: CurriculumContract, result: ValidationResult
) -> None:
    n = len(contract.golden_case_ids)
    if n < MIN_GOLDEN_CASES:
        result.add(
            ValidationCode.INSUFFICIENT_GOLDEN_CASES,
            f"{n} golden cases present; at least {MIN_GOLDEN_CASES} required",
        )


def _rule_7_not_yet_boundaries(
    contract: CurriculumContract, result: ValidationResult
) -> None:
    for method in contract.methods.not_yet_introduced:
        if not method.response_rule.strip():
            result.add(
                ValidationCode.NOT_YET_INTRODUCED_MISSING_BOUNDARY,
                f"not-yet-introduced method {method.id!r} has no boundary/teacher note",
            )


def _rule_8_privacy_config(
    contract: CurriculumContract, result: ValidationResult
) -> None:
    analytics = contract.analytics
    if analytics.retention_days is None or analytics.retention_days <= 0:
        result.add(
            ValidationCode.PRIVACY_NOT_CONFIGURED,
            "retention_days is not explicitly configured",
        )
    if not (analytics.raw_chat_visibility or "").strip():
        result.add(
            ValidationCode.PRIVACY_NOT_CONFIGURED,
            "raw_chat_visibility is not explicitly configured",
        )
