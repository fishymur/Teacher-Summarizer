"""Tenant-scoped data access.

Invariant #1 (section 11): *every* query is filtered by ``tenant_id`` at the
data-access layer. Callers never issue raw cross-tenant queries; they obtain a
``TenantRepository`` bound to a single tenant, and every read filters and every
write stamps that tenant id. A lookup for a row owned by another tenant returns
nothing, exactly as if the row did not exist.
"""

from __future__ import annotations

from typing import Optional, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base

T = TypeVar("T", bound=Base)


class CrossTenantWrite(RuntimeError):
    """Raised when a row's tenant_id disagrees with the repository's tenant."""


class TenantRepository:
    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def add(self, row: T) -> T:
        # Stamp/verify tenant scope on write. Models without tenant_id (only the
        # tenant itself) are added as-is.
        if hasattr(row, "tenant_id"):
            current = getattr(row, "tenant_id", None)
            if current is None:
                setattr(row, "tenant_id", self._tenant_id)
            elif current != self._tenant_id:
                raise CrossTenantWrite(
                    f"row tenant_id {current!r} != repository tenant {self._tenant_id!r}"
                )
        self._session.add(row)
        return row

    def get(self, model: Type[T], pk: str) -> Optional[T]:
        row = self._session.get(model, pk)
        if row is None:
            return None
        # Filter at the access layer: a row from another tenant is invisible.
        if hasattr(row, "tenant_id") and getattr(row, "tenant_id") != self._tenant_id:
            return None
        return row

    def list(self, model: Type[T], **filters) -> list[T]:
        stmt = select(model)
        if hasattr(model, "tenant_id"):
            stmt = stmt.where(model.tenant_id == self._tenant_id)
        for key, value in filters.items():
            stmt = stmt.where(getattr(model, key) == value)
        return list(self._session.scalars(stmt).all())

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()
