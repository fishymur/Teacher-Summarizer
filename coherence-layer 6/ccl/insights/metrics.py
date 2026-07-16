"""Signal-quality metrics (section 9.5).

These measure whether the insight layer is worth a teacher's attention:

- actionability / precision: how often reviewed insights were confirmed;
- correction rate: how often teachers flagged an insight as wrong or misleading;
- privacy exceptions: how often raw-transcript access was used (should be rare).

All are computed from persisted reviews, corrections, and the audit log — never
from anything that would require reading an individual student's messages.
"""

from __future__ import annotations

from ..data.audit import AuditLog
from ..data.insight_models import TeacherCorrection, TeacherInsight
from ..data.repository import TenantRepository
from .types import CorrectionKind, InsightStatus


def signal_quality(
    repo: TenantRepository, audit: AuditLog, course_id: str
) -> dict[str, float | int]:
    insights = repo.list(TeacherInsight, course_id=course_id)
    reviewed = [
        i for i in insights
        if i.status in (InsightStatus.CONFIRMED.value, InsightStatus.DISMISSED.value)
    ]
    confirmed = [i for i in reviewed if i.status == InsightStatus.CONFIRMED.value]

    corrections = repo.list(TeacherCorrection, course_id=course_id)
    misleading = [
        c for c in corrections
        if c.kind in (CorrectionKind.MISLEADING_ANALYTICS.value,
                      CorrectionKind.WRONG_MISCONCEPTION.value)
    ]

    privacy_exceptions = sum(
        1 for e in audit.events() if e.action == "privacy.raw_transcript_access"
    )

    n = len(reviewed) or 1
    return {
        "insights_total": len(insights),
        "insights_reviewed": len(reviewed),
        "actionability": round(len(confirmed) / n, 2),
        "precision": round(len(confirmed) / n, 2),
        "correction_rate": round(len(misleading) / n, 2),
        "privacy_exceptions": privacy_exceptions,
    }
