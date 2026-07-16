"""Provider registry persistence (sections 11, 15, 17).

An admin-visible record of each model the school has vetted: where it runs, how
long the provider retains data, whether provider-side training is disabled,
which subprocessors are involved, and what uses are approved. A provider must be
*registered and approved* before the runtime may call it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ProviderRecord(Base):
    __tablename__ = "provider_record"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False, default="")
    region: Mapped[str] = mapped_column(String, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    training_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subprocessors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    approved_uses_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String, nullable=False, default="registered")  # registered|approved|revoked
    eval_run_id: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
