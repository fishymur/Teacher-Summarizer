"""Audit log service and a DB-backed anchor resolver.

The audit log writes append-only ``AuditEvent`` rows (enforced by the mapper
event in models.py). The anchor resolver adapts stored materials to the
``AnchorResolver`` protocol the contract validator expects.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from .models import AuditEvent, SourceAnchor, MaterialVersion, Material
from .repository import TenantRepository


class AuditLog:
    def __init__(self, repo: TenantRepository) -> None:
        self._repo = repo

    def record(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        actor_id: str | None = None,
        detail: str = "",
    ) -> AuditEvent:
        evt = AuditEvent(
            id=f"evt_{uuid.uuid4().hex[:12]}",
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
        self._repo.add(evt)
        self._repo.flush()
        return evt

    def events(self) -> list[AuditEvent]:
        return self._repo.list(AuditEvent)


class DBAnchorResolver:
    """Resolves ``material_id:anchor`` refs against stored material versions."""

    def __init__(self, repo: TenantRepository) -> None:
        self._repo = repo

    def resolves(self, source_ref: str, approved_material_ids: list[str]) -> bool:
        material_id, _, anchor = source_ref.partition(":")
        if material_id not in approved_material_ids:
            return False
        if not anchor:
            return False
        material = self._repo.get(Material, material_id)
        if material is None:
            return False
        # An anchor ref like "p12-p14" resolves if its start anchor exists on the
        # latest version of the material.
        start_label = anchor.split("-", 1)[0]
        session = self._repo._session  # access-layer helper
        version = session.scalars(
            select(MaterialVersion)
            .where(MaterialVersion.material_id == material_id)
            .where(MaterialVersion.tenant_id == self._repo.tenant_id)
            .order_by(MaterialVersion.version.desc())
        ).first()
        if version is None:
            return False
        labels = {
            a.label
            for a in session.scalars(
                select(SourceAnchor)
                .where(SourceAnchor.material_version_id == version.id)
                .where(SourceAnchor.tenant_id == self._repo.tenant_id)
            ).all()
        }
        return start_label in labels
