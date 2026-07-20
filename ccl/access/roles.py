"""Roles, permissions, and the permission matrix (sections 3 and 17).

Design choices, made explicit because a school's privacy reviewer will ask:

- **Least privilege.** Each role gets only the permissions its job needs
  (section 3). A student can use the tutor and read their own transcript, and
  nothing else.
- **Separation of duties.** The ``admin`` role configures the system (roles,
  retention, providers, audit) but deliberately does NOT get to author or
  publish curriculum, review learning insights, or read student transcripts.
  Curriculum authority sits with ``teacher`` / ``chair``; the person who
  controls the plumbing is not automatically the person who reads kids' data.
- **Course scoping.** Teacher permissions are scoped to the courses they are
  assigned to. A grant with ``course_id=None`` is department/tenant-wide and is
  how a ``chair`` gets cross-course reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    CHAIR = "chair"
    ADMIN = "admin"


class Permission(str, Enum):
    # course-scoped
    MATERIAL_IMPORT = "material_import"
    CONTRACT_AUTHOR = "contract_author"
    CONTRACT_APPROVE = "contract_approve"
    CONTRACT_PUBLISH = "contract_publish"
    TUTOR_USE = "tutor_use"
    TUTOR_PLAYGROUND = "tutor_playground"
    INSIGHT_VIEW = "insight_view"
    INSIGHT_REVIEW = "insight_review"
    CORRECTION_SUBMIT = "correction_submit"
    TRANSCRIPT_ACCESS_OWN = "transcript_access_own"
    TRANSCRIPT_ACCESS_ESCALATED = "transcript_access_escalated"
    # tenant-level (not course-scoped)
    COURSE_CREATE = "course_create"
    ROLE_MANAGE = "role_manage"
    RETENTION_MANAGE = "retention_manage"
    PROVIDER_MANAGE = "provider_manage"
    AUDIT_VIEW = "audit_view"


# Permissions that apply tenant-wide rather than per course.
TENANT_LEVEL_PERMISSIONS: set[Permission] = {
    Permission.COURSE_CREATE,
    Permission.ROLE_MANAGE,
    Permission.RETENTION_MANAGE,
    Permission.PROVIDER_MANAGE,
    Permission.AUDIT_VIEW,
}

_TEACHER_PERMS = {
    Permission.MATERIAL_IMPORT,
    Permission.CONTRACT_AUTHOR,
    Permission.CONTRACT_APPROVE,
    Permission.CONTRACT_PUBLISH,
    Permission.TUTOR_PLAYGROUND,
    Permission.INSIGHT_VIEW,
    Permission.INSIGHT_REVIEW,
    Permission.CORRECTION_SUBMIT,
    Permission.TRANSCRIPT_ACCESS_ESCALATED,
}

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.STUDENT: {
        Permission.TUTOR_USE,
        Permission.TRANSCRIPT_ACCESS_OWN,
    },
    Role.TEACHER: set(_TEACHER_PERMS),
    # A chair is a teacher with cross-course reach plus audit visibility and the
    # ability to create courses. Reach comes from a course_id=None grant.
    Role.CHAIR: set(_TEACHER_PERMS) | {Permission.COURSE_CREATE, Permission.AUDIT_VIEW},
    # Admin runs the plumbing; no curriculum authority, no student learning data.
    Role.ADMIN: {
        Permission.COURSE_CREATE,
        Permission.ROLE_MANAGE,
        Permission.RETENTION_MANAGE,
        Permission.PROVIDER_MANAGE,
        Permission.AUDIT_VIEW,
    },
}


def role_has(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


@dataclass(frozen=True)
class RoleGrant:
    role: Role
    course_id: str | None = None  # None = tenant/department-wide


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    grants: tuple[RoleGrant, ...] = ()

    @property
    def roles(self) -> set[Role]:
        return {g.role for g in self.grants}
