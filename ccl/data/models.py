"""ORM models for the milestone-1 data foundation.

Invariants enforced here (section 11):
- every row carries ``tenant_id`` (see the repository for access-layer filtering);
- ``SourceChunk`` rows are immutable within a material version;
- ``AuditEvent`` rows are append-only.

Immutability and append-only are enforced with SQLAlchemy mapper events so they
hold no matter which code path attempts a write.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class SchoolTenant(Base):
    __tablename__ = "school_tenant"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class User(Base):
    __tablename__ = "user"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)


class RoleAssignment(Base):
    __tablename__ = "role_assignment"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"))
    role: Mapped[str] = mapped_column(String, nullable=False)  # teacher|student|admin|chair
    course_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class Course(Base):
    __tablename__ = "course"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Section(Base):
    __tablename__ = "section"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    name: Mapped[str] = mapped_column(String, nullable=False)


class Enrollment(Base):
    __tablename__ = "enrollment"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("section.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"))
    role: Mapped[str] = mapped_column(String, nullable=False)


class Material(Base):
    __tablename__ = "material"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    title: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # pdf|slides|doc|assignment

    versions: Mapped[list["MaterialVersion"]] = relationship(
        back_populates="material", cascade="all, delete-orphan"
    )


class MaterialVersion(Base):
    __tablename__ = "material_version"
    __table_args__ = (UniqueConstraint("material_id", "version", name="uq_material_version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("material.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    checksum: Mapped[str] = mapped_column(String, nullable=False)

    material: Mapped["Material"] = relationship(back_populates="versions")
    anchors: Mapped[list["SourceAnchor"]] = relationship(
        back_populates="material_version", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["SourceChunk"]] = relationship(
        back_populates="material_version", cascade="all, delete-orphan"
    )


class SourceAnchor(Base):
    """A stable, resolvable location within a material version, e.g. ``p12``."""

    __tablename__ = "source_anchor"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    material_version_id: Mapped[str] = mapped_column(ForeignKey("material_version.id"))
    label: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "p12", "slide_4"
    kind: Mapped[str] = mapped_column(String, nullable=False)  # page|slide|section

    material_version: Mapped["MaterialVersion"] = relationship(back_populates="anchors")


class SourceChunk(Base):
    """Immutable text chunk bound to an anchor within a material version."""

    __tablename__ = "source_chunk"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    material_version_id: Mapped[str] = mapped_column(ForeignKey("material_version.id"))
    anchor_label: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    material_version: Mapped["MaterialVersion"] = relationship(back_populates="chunks")


class CurriculumContractRow(Base):
    """Persisted contract. The intent document itself is stored as JSON so the
    Pydantic schema remains the single source of truth for its shape."""

    __tablename__ = "curriculum_contract"
    __table_args__ = (
        UniqueConstraint("course_id", "version", name="uq_contract_course_version"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)  # contract_id
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class ContractApproval(Base):
    __tablename__ = "contract_approval"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("curriculum_contract.id"))
    approved_by: Mapped[str] = mapped_column(ForeignKey("user.id"))
    approved_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class AuditEvent(Base):
    """Append-only record of a sensitive action."""

    __tablename__ = "audit_event"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class ImmutableRowError(RuntimeError):
    """Raised when code attempts to mutate an append-only or immutable row."""


# --- Enforcement of the append-only / immutable invariants ------------------

def _block_update(mapper, connection, target) -> None:  # noqa: ANN001
    raise ImmutableRowError(
        f"{type(target).__name__} rows are immutable and cannot be updated"
    )


def _block_delete(mapper, connection, target) -> None:  # noqa: ANN001
    raise ImmutableRowError(
        f"{type(target).__name__} rows are append-only and cannot be deleted"
    )


for _model in (SourceChunk, AuditEvent):
    event.listen(_model, "before_update", _block_update)

# Audit is append-only (no deletes). Source chunks may be removed only by
# deleting their parent material version (retention/deletion policy), so we do
# not block chunk deletes here.
event.listen(AuditEvent, "before_delete", _block_delete)
