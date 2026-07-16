"""Hint planner (section 8, pipeline step 6; hint ladder section 7).

Turns the policy ceiling and the student's state into a *target* hint level.
Encodes two product principles: diagnose before explaining, and preserve
productive struggle. When an attempt is required but absent, the plan caps help
at the diagnostic rungs no matter what the mode would otherwise permit — this is
the behaviour the Bastani field evidence motivates (no answer dumping).
"""

from __future__ import annotations

from dataclasses import dataclass

from .classifier import RequestType

DIAGNOSTIC_CEILING = 2  # levels 1-2: diagnose / prompt an attempt


def is_meaningful_attempt(attempt: str) -> bool:
    return len((attempt or "").strip()) >= 4


@dataclass
class Plan:
    target_hint_level: int
    requires_attempt_first: bool
    rationale: str


def plan_hint(
    *,
    request_type: RequestType,
    mode_ceiling: int,
    require_student_attempt: bool,
    student_attempt: str,
) -> Plan:
    has_attempt = is_meaningful_attempt(student_attempt)

    # Attempt gate: no meaningful attempt + attempts required -> diagnostic only.
    if require_student_attempt and not has_attempt:
        target = min(DIAGNOSTIC_CEILING, mode_ceiling)
        return Plan(
            target_hint_level=target,
            requires_attempt_first=True,
            rationale="attempt required but none provided; diagnose/prompt first",
        )

    if request_type is RequestType.SOLUTION_REQUEST and not has_attempt:
        target = min(DIAGNOSTIC_CEILING, mode_ceiling)
        return Plan(
            target_hint_level=target,
            requires_attempt_first=True,
            rationale="solution requested without an attempt; withhold and diagnose",
        )

    if request_type is RequestType.CLARIFICATION:
        return Plan(min(2, mode_ceiling), False, "clarification needs no escalation")

    # With an attempt present, allow escalation up to the mode ceiling but start
    # one rung below a full solution so struggle is preserved by default.
    target = max(1, min(mode_ceiling, mode_ceiling if has_attempt else 3))
    if has_attempt and mode_ceiling >= 6:
        target = 5  # leave the full-solution rung for an explicit later step
    return Plan(target, False, "attempt present; escalate within ceiling")
