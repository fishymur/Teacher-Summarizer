"""Retention purge (sections 9.3 and 17).

Applies each data class's retention period. The key privacy property is the
*separation*: raw student content is redacted on the shortest clock while the
compliance trace and the de-identified aggregates survive longer.

Order of erosion for a tutor message:
1. after ``raw_messages`` days   -> redact the student's words and the reply text
                                     (the row and its policy/verifier trace remain);
2. after ``policy_traces`` days  -> delete the row entirely.

Audit events are never purged here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..data.audit import AuditLog
from ..data.insight_models import TeacherInsight
from ..data.repository import TenantRepository
from ..data.tutor_models import InteractionEvent, TutorMessage
from .config import RetentionConfig, load_config

REDACTED = "[redacted-by-retention]"


@dataclass
class PurgeReport:
    raw_redacted: int = 0
    messages_deleted: int = 0
    events_deleted: int = 0
    insights_deleted: int = 0
    actions: list[str] = field(default_factory=list)


class RetentionService:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    def purge(
        self, *, now: dt.datetime | None = None, config: RetentionConfig | None = None
    ) -> PurgeReport:
        now = now or dt.datetime.now(dt.timezone.utc)
        cfg = config or load_config(self._repo)
        report = PurgeReport()

        raw_days = cfg.days("raw_messages")
        trace_days = cfg.days("policy_traces")
        for msg in self._repo.list(TutorMessage):
            age = _age_days(msg.created_at, now)
            if trace_days is not None and age >= trace_days:
                self._repo._session.delete(msg)
                report.messages_deleted += 1
            elif raw_days is not None and age >= raw_days and msg.student_message != REDACTED:
                # Redact raw content; keep the policy/verifier trace.
                msg.student_message = REDACTED
                msg.response_text = REDACTED
                msg.citations_json = "[]"
                report.raw_redacted += 1

        ev_days = cfg.days("interaction_events")
        if ev_days is not None:
            for ev in self._repo.list(InteractionEvent):
                if _age_days(ev.created_at, now) >= ev_days:
                    self._repo._session.delete(ev)
                    report.events_deleted += 1

        agg_days = cfg.days("aggregate_insights")
        if agg_days is not None:
            for ins in self._repo.list(TeacherInsight):
                if _age_days(ins.created_at, now) >= agg_days:
                    self._repo._session.delete(ins)
                    report.insights_deleted += 1

        self._repo.flush()
        report.actions = [
            f"raw_redacted={report.raw_redacted}",
            f"messages_deleted={report.messages_deleted}",
            f"events_deleted={report.events_deleted}",
            f"insights_deleted={report.insights_deleted}",
        ]
        self._audit.record(
            action="privacy.retention.purge",
            target_type="Tenant",
            target_id=self._repo.tenant_id,
            detail="; ".join(report.actions),
        )
        return report


def _age_days(ts: dt.datetime, now: dt.datetime) -> float:
    if ts.tzinfo is None and now.tzinfo is not None:
        ts = ts.replace(tzinfo=now.tzinfo)
    return (now - ts).total_seconds() / 86400.0
