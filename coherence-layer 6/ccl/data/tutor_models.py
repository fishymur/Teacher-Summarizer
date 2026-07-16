"""Tutor runtime persistence (section 11 entities).

Each ``TutorMessage`` stores the published contract version that governed it,
plus the policy trace, citations, and verifier result as JSON so every response
is auditable after the fact. ``InteractionEvent`` rows feed the (later) insight
pipeline; they are emitted aggregately and never trigger a synchronous
per-student diagnosis.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TutorSession(Base):
    __tablename__ = "tutor_session"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    student_id: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    contract_version_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class TutorMessage(Base):
    __tablename__ = "tutor_message"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("tutor_session.id"))
    contract_version_id: Mapped[str] = mapped_column(String, nullable=False)
    student_message: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    hint_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    outcome: Mapped[str] = mapped_column(String, nullable=False)  # answered|revised|fallback
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    policy_trace_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    verifier_result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    latency_ms: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class InteractionEvent(Base):
    __tablename__ = "interaction_event"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    concept_id: Mapped[str] = mapped_column(String, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
