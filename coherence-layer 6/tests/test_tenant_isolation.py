"""Acceptance: tenant isolation is enforced at the data-access layer.

Maps to build-context section 11, invariant #1: every row carries a tenant_id
and is filtered at the access layer. A row owned by tenant B must be invisible
to a repository bound to tenant A, and writing a foreign row must be refused.
"""

from ccl.data import CrossTenantWrite, TenantRepository
from ccl.data.models import Course, SchoolTenant


def _two_tenants(session):
    session.add(SchoolTenant(id="school_a", name="A"))
    session.add(SchoolTenant(id="school_b", name="B"))
    session.flush()
    return TenantRepository(session, "school_a"), TenantRepository(session, "school_b")


def test_other_tenant_rows_are_invisible(session):
    repo_a, repo_b = _two_tenants(session)
    repo_b.add(Course(id="course_b", name="B course", subject="math"))
    repo_b.flush()

    # Tenant A cannot see tenant B's course, even by primary key.
    assert repo_a.get(Course, "course_b") is None
    assert repo_a.list(Course) == []
    # Tenant B can.
    assert repo_b.get(Course, "course_b") is not None
    assert len(repo_b.list(Course)) == 1


def test_add_stamps_repository_tenant(session):
    repo_a, _ = _two_tenants(session)
    course = Course(id="course_a", name="A course", subject="math")
    repo_a.add(course)
    repo_a.flush()
    assert course.tenant_id == "school_a"


def test_cross_tenant_write_is_refused(session):
    repo_a, _ = _two_tenants(session)
    foreign = Course(id="course_x", tenant_id="school_b", name="X", subject="math")
    try:
        repo_a.add(foreign)
    except CrossTenantWrite:
        pass
    else:
        raise AssertionError("expected CrossTenantWrite for a foreign-tenant row")
