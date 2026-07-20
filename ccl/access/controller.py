"""Access controller (section 17).

``can`` is a pure predicate. ``require`` is the enforcement point: it raises
``AccessDenied`` and writes an append-only ``access.denied`` audit event, so that
every refused attempt on sensitive functionality leaves a trail.
"""

from __future__ import annotations

from ..data.audit import AuditLog
from .roles import (
    TENANT_LEVEL_PERMISSIONS,
    Permission,
    Principal,
    role_has,
)


class AccessDenied(PermissionError):
    def __init__(self, principal: Principal, permission: Permission, course_id: str | None):
        self.permission = permission
        self.course_id = course_id
        super().__init__(
            f"user {principal.user_id!r} lacks {permission.value!r}"
            + (f" for course {course_id!r}" if course_id else "")
        )


class AccessController:
    def __init__(self, audit: AuditLog | None = None) -> None:
        self._audit = audit

    def can(
        self, principal: Principal, permission: Permission, course_id: str | None = None
    ) -> bool:
        tenant_level = permission in TENANT_LEVEL_PERMISSIONS
        for grant in principal.grants:
            if not role_has(grant.role, permission):
                continue
            if tenant_level:
                return True
            # Course-scoped. If the caller specified no course, the check is not
            # scoped to a particular course (e.g. "read my own transcript"), so
            # any grant holding the permission satisfies it. If a course *is*
            # given, a department-wide (None) grant matches, otherwise the course
            # must match exactly.
            if course_id is None or grant.course_id is None or grant.course_id == course_id:
                return True
        return False

    def require(
        self,
        principal: Principal,
        permission: Permission,
        course_id: str | None = None,
        *,
        action: str = "",
    ) -> None:
        if self.can(principal, permission, course_id):
            return
        if self._audit is not None:
            self._audit.record(
                action="access.denied",
                target_type="Permission",
                target_id=permission.value,
                actor_id=principal.user_id,
                detail=f"{action} course={course_id}".strip(),
            )
        raise AccessDenied(principal, permission, course_id)
