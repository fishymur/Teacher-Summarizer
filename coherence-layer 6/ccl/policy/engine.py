"""Deterministic policy engine.

Given a *published* Curriculum Contract and a request context, produce the
``PolicyDecision`` shape from section 14. The engine is pure and deterministic:
identical inputs always yield an identical decision, which is what lets the
same contract version govern the tutor, the evaluation harness, and the audit
trail consistently.

This is the executable-pedagogy core. It stops short of generation: it decides
*what the runtime is permitted to do*, not what it says.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts.lifecycle import is_governing
from ..contracts.schema import CurriculumContract
from .rules import compile_contract, rule_matches


class ContractNotGoverning(ValueError):
    """Raised when asked to make a decision from a non-published contract."""


@dataclass
class RequestContext:
    mode: str
    concept_ids: list[str] = field(default_factory=list)
    current_unit: str | None = None
    unit_order: list[str] | None = None


@dataclass
class PolicyDecision:
    mode: str
    max_hint_level: int
    required_method_ids: list[str] = field(default_factory=list)
    forbidden_method_ids: list[str] = field(default_factory=list)
    allowed_source_ids: list[str] = field(default_factory=list)
    external_sources_allowed: bool = False
    full_solution_allowed: bool = False
    # Human-readable boundary messages keyed by the rule that produced them.
    student_explanations: dict[str, str] = field(default_factory=dict)
    # Rule ids that fired, for audit and the section 14 "explanations" field.
    explanations: list[str] = field(default_factory=list)


# Modes in which a full solution may be released (subject to attempt gating that
# the tutor runtime applies later). Practice and Assessment never release one
# from policy alone.
_FULL_SOLUTION_MODES = {"learn", "review"}


class PolicyEngine:
    def decide(
        self, contract: CurriculumContract, context: RequestContext
    ) -> PolicyDecision:
        if not is_governing(contract.status):
            raise ContractNotGoverning(
                "policy decisions may only be made from a published contract; "
                f"got status {contract.status.value!r}"
            )

        rules = compile_contract(contract)

        max_hint_level: int | None = None
        required: list[str] = []
        forbidden: list[str] = []
        allowed_sources: list[str] = []
        external_allowed = False
        explanations: list[str] = []
        student_explanations: dict[str, str] = {}

        # Evaluate in priority order so higher-priority rules (e.g. prohibitions)
        # are applied last and win deterministically.
        for rule in sorted(rules, key=lambda r: r.priority):
            if not rule_matches(
                rule,
                mode=context.mode,
                concept_ids=context.concept_ids,
                current_unit=context.current_unit,
                unit_order=context.unit_order,
            ):
                continue

            fired = False
            effect = rule.effect

            if "require_method_ids" in effect:
                for mid in effect["require_method_ids"]:
                    if mid not in required:
                        required.append(mid)
                fired = True

            if "forbid_method_ids" in effect:
                for mid in effect["forbid_method_ids"]:
                    if mid not in forbidden:
                        forbidden.append(mid)
                fired = True

            if "max_hint_level" in effect:
                max_hint_level = effect["max_hint_level"]
                fired = True

            if "allowed_source_ids" in effect:
                allowed_sources = list(effect["allowed_source_ids"])
                fired = True

            if "external_sources_allowed" in effect:
                external_allowed = bool(effect["external_sources_allowed"])
                fired = True

            if "student_explanation" in effect:
                student_explanations[rule.rule_id] = effect["student_explanation"]

            if fired:
                explanations.append(rule.rule_id)

        if max_hint_level is None:
            # No ceiling rule matched the mode: fail closed to the strictest
            # level rather than granting unbounded help.
            max_hint_level = 1

        full_solution_allowed = (
            context.mode in _FULL_SOLUTION_MODES and max_hint_level >= 6
        )

        # A forbidden method can never also be required.
        required = [mid for mid in required if mid not in forbidden]

        return PolicyDecision(
            mode=context.mode,
            max_hint_level=max_hint_level,
            required_method_ids=required,
            forbidden_method_ids=forbidden,
            allowed_source_ids=allowed_sources,
            external_sources_allowed=external_allowed,
            full_solution_allowed=full_solution_allowed,
            student_explanations=student_explanations,
            explanations=explanations,
        )
