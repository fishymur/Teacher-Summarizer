"""Retention configuration (section 9.3).

Separate retention periods per data class. The defaults encode the spec's
intent: raw student content lives on the shortest clock, de-identified
aggregates live longer, and the audit log is never purged.

Data classes:
- ``raw_messages``      the student's words and the tutor's reply text
- ``policy_traces``     the message row minus raw content (policy + verifier)
- ``interaction_events`` behavioural events feeding the insight pipeline
- ``aggregate_insights`` de-identified misconception clusters
- ``evaluation_cases``  de-identified teacher-authored cases (kept by default)

``audit_events`` are deliberately absent: they are append-only and retained.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..data.privacy_models import RetentionPolicyRow
from ..data.repository import TenantRepository

DEFAULTS: dict[str, int | None] = {
    "raw_messages": 180,
    "policy_traces": 365,
    "interaction_events": 90,
    "aggregate_insights": 730,
    "evaluation_cases": None,  # None = keep indefinitely (de-identified)
}


@dataclass
class RetentionConfig:
    periods: dict[str, int | None] = field(default_factory=lambda: dict(DEFAULTS))

    def days(self, data_class: str) -> int | None:
        return self.periods.get(data_class, DEFAULTS.get(data_class))


def load_config(repo: TenantRepository) -> RetentionConfig:
    cfg = RetentionConfig()
    for row in repo.list(RetentionPolicyRow):
        cfg.periods[row.data_class] = row.retention_days
    return cfg


def save_config(repo: TenantRepository, config: RetentionConfig) -> None:
    existing = {r.data_class: r for r in repo.list(RetentionPolicyRow)}
    for data_class, days in config.periods.items():
        if days is None:
            continue
        row = existing.get(data_class)
        if row is None:
            repo.add(RetentionPolicyRow(
                id=f"ret_{uuid.uuid4().hex[:10]}", data_class=data_class, retention_days=days,
            ))
        else:
            row.retention_days = days
    repo.flush()
