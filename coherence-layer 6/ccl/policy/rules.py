"""Compiles a Curriculum Contract into structured predicate rules.

Per section 14, a rule must never live only as prompt text. Every enforceable
part of the contract is compiled into a ``CompiledRule`` with an explicit
``when`` predicate and ``effect``. The policy engine evaluates these
deterministically; the tutor runtime (a later milestone) consumes the resulting
decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..contracts.schema import CurriculumContract


@dataclass(frozen=True)
class CompiledRule:
    rule_id: str
    type: str
    priority: int
    when: dict[str, Any] = field(default_factory=dict)
    effect: dict[str, Any] = field(default_factory=dict)


def _unit_index(unit_id: str, unit_order: list[str] | None) -> int:
    """Return an orderable index for a unit id.

    Prefer an explicit unit_order supplied by the caller. Fall back to the
    trailing integer in an id like ``unit_6``. Unknown units sort last so a
    missing unit never silently unlocks a restricted method.
    """
    if unit_order and unit_id in unit_order:
        return unit_order.index(unit_id)
    match = re.search(r"(\d+)$", unit_id)
    if match:
        return int(match.group(1))
    return 10**6


def compile_contract(contract: CurriculumContract) -> list[CompiledRule]:
    rules: list[CompiledRule] = []
    m = contract.methods

    # Preferred methods become "require this method for these concepts" rules.
    for method in m.preferred:
        rules.append(
            CompiledRule(
                rule_id=f"rule_{method.id}_preferred",
                type="preferred_method",
                priority=50,
                when={"concept_ids_any": list(method.applies_to)},
                effect={"require_method_ids": [method.id]},
            )
        )

    # Not-yet-introduced methods forbid the method while the current unit is
    # before the unlock unit.
    for method in m.not_yet_introduced:
        rules.append(
            CompiledRule(
                rule_id=f"rule_{method.id}_not_yet",
                type="method_not_yet_introduced",
                priority=90,
                when={
                    "concept_ids_any": list(method.applies_to),
                    "current_unit_before": method.until_unit,
                },
                effect={
                    "forbid_method_ids": [method.id],
                    "student_explanation": method.response_rule,
                },
            )
        )

    # Prohibited methods are always forbidden.
    for method in m.prohibited:
        rules.append(
            CompiledRule(
                rule_id=f"rule_{method.id}_prohibited",
                type="method_prohibited",
                priority=100,
                when={"concept_ids_any": list(method.applies_to)},
                effect={"forbid_method_ids": [method.id]},
            )
        )

    # Mode hint ceilings.
    for mode, ceiling in contract.pedagogy.maximum_hint_level_by_mode.items():
        rules.append(
            CompiledRule(
                rule_id=f"rule_{mode}_hint_ceiling",
                type="mode_hint_ceiling",
                priority=60,
                when={"mode": mode},
                effect={"max_hint_level": ceiling},
            )
        )

    # Source scope.
    rules.append(
        CompiledRule(
            rule_id="rule_source_scope",
            type="source_scope",
            priority=40,
            when={},
            effect={
                "allowed_source_ids": list(contract.source_policy.approved_material_ids),
                "external_sources_allowed": contract.source_policy.external_sources
                == "enabled",
            },
        )
    )

    return rules


def rule_matches(
    rule: CompiledRule,
    *,
    mode: str,
    concept_ids: list[str],
    current_unit: str | None,
    unit_order: list[str] | None,
) -> bool:
    when = rule.when

    if "mode" in when and when["mode"] != mode:
        return False

    if "concept_ids_any" in when:
        required = when["concept_ids_any"]
        # An empty applies_to means the rule is course-wide (matches any concept).
        if required and not (set(required) & set(concept_ids)):
            return False

    if "current_unit_before" in when:
        if current_unit is None:
            # Without a known current unit we conservatively treat the method as
            # still restricted (fail closed).
            return True
        unlock = when["current_unit_before"]
        if _unit_index(current_unit, unit_order) >= _unit_index(unlock, unit_order):
            return False

    return True
