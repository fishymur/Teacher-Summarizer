"""Privacy persistence (section 11 entities).

- ``RetentionPolicyRow`` stores a per-tenant retention period for one data class
  (raw messages, interaction events, aggregate insights, ...). Section 9.3
  requires these to be configurable *and separate* from each other.
- ``DeletionRequest`` records a right-to-erasure request for one data subject and
  its completion, so the erasure itself is auditable.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class RetentionPolicyRow(Base):
    __tablename__ = "retention_policy"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    data_class: Mapped[str] = mapped_column(String, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)


class DeletionRequest(Base):
    __tablename__ = "deletion_request"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    subject_user_id: Mapped[str] = mapped_column(String, nullable=False)
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False, default="learning_data")
    status: Mapped[str] = mapped_column(String, nullable=False, default="requested")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
