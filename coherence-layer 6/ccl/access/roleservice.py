"""Role administration (section 17).

Grants and revokes are tenant-scoped (via the repository) and audited. Loading a
principal reads that user's role assignments and turns them into the immutable
``Principal`` the controller checks against.
"""

from __future__ import annotations

import uuid

from ..data.audit import AuditLog
from ..data.models import RoleAssignment
from ..data.repository import TenantRepository
from .roles import Principal, Role, RoleGrant


class RoleService:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    def grant(
        self, user_id: str, role: Role, course_id: str | None, actor_id: str
    ) -> RoleAssignment:
        row = RoleAssignment(
            id=f"role_{uuid.uuid4().hex[:10]}",
            user_id=user_id,
            role=role.value,
            course_id=course_id,
        )
        self._repo.add(row)
        self._repo.flush()
        self._audit.record(
            action="role.grant",
            target_type="RoleAssignment",
            target_id=row.id,
            actor_id=actor_id,
            detail=f"{user_id}:{role.value}:course={course_id}",
        )
        return row

    def revoke(self, assignment_id: str, actor_id: str) -> None:
        row = self._repo.get(RoleAssignment, assignment_id)
        if row is None:
            raise KeyError(assignment_id)
        self._repo._session.delete(row)
        self._repo.flush()
        self._audit.record(
            action="role.revoke",
            target_type="RoleAssignment",
            target_id=assignment_id,
            actor_id=actor_id,
        )

    def load_principal(self, user_id: str) -> Principal:
        rows = self._repo.list(RoleAssignment, user_id=user_id)
        grants = tuple(RoleGrant(Role(r.role), r.course_id) for r in rows)
        return Principal(user_id=user_id, tenant_id=self._repo.tenant_id, grants=grants)
