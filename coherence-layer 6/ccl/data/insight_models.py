"""Teacher-insight persistence (section 10, 11 entities).

- ``TeacherInsight`` is a surfaced, evidence-backed aggregate pattern awaiting
  teacher review. It never stores per-student identifiers.
- ``TeacherCorrection`` records a teacher's judgement on an insight or response
  and the follow-up it produced (a draft contract change or an evaluation case).
  A correction never mutates a published contract.
- ``EvaluationCaseRow`` persists a teacher-authored case (e.g. produced from a
  correction) so the evaluation harness can pick it up on the next run.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TeacherInsight(Base):
    __tablename__ = "teacher_insight"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    window_start: Mapped[str] = mapped_column(String, nullable=False)
    window_end: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # misconception_cluster|prerequisite_gap|...
    concept_id: Mapped[str] = mapped_column(String, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supporting_event_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    observed_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    suggested_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending_teacher_review")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class TeacherCorrection(Base):
    __tablename__ = "teacher_correction"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    target_type: Mapped[str] = mapped_column(String, nullable=False)  # insight|message
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resulting_action: Mapped[str] = mapped_column(String, nullable=False, default="none")
    resulting_ref: Mapped[str] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class EvaluationCaseRow(Base):
    __tablename__ = "evaluation_case"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("school_tenant.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    contract_version_id: Mapped[str] = mapped_column(String, nullable=False)
    case_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="golden")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
