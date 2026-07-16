"""Contract lifecycle state machine.

    draft -> validating -> approved -> published -> superseded -> archived

Only a *published* version may govern student sessions. Transitions are
explicit so that publishing a new version supersedes the prior one rather than
mutating past behaviour.
"""

from __future__ import annotations

from .schema import ContractStatus

# Allowed forward transitions. Anything not listed is rejected.
ALLOWED_TRANSITIONS: dict[ContractStatus, set[ContractStatus]] = {
    ContractStatus.DRAFT: {ContractStatus.VALIDATING, ContractStatus.ARCHIVED},
    ContractStatus.VALIDATING: {
        ContractStatus.APPROVED,
        ContractStatus.DRAFT,  # send back for edits if validation fails
        ContractStatus.ARCHIVED,
    },
    ContractStatus.APPROVED: {ContractStatus.PUBLISHED, ContractStatus.ARCHIVED},
    ContractStatus.PUBLISHED: {ContractStatus.SUPERSEDED, ContractStatus.ARCHIVED},
    ContractStatus.SUPERSEDED: {ContractStatus.ARCHIVED},
    ContractStatus.ARCHIVED: set(),
}


class IllegalTransition(ValueError):
    """Raised when a lifecycle transition is not permitted."""


def assert_transition(current: ContractStatus, target: ContractStatus) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise IllegalTransition(
            f"cannot move contract from {current.value!r} to {target.value!r}"
        )


def is_governing(status: ContractStatus) -> bool:
    """Only a published contract governs runtime behaviour."""
    return status == ContractStatus.PUBLISHED
