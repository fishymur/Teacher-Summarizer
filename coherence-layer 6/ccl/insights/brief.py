"""Weekly insight brief (section 9.C).

Assembles the teacher-facing brief from aggregated events: top misconception
clusters, prerequisite gaps, full-solution pressure, out-of-scope volume, and
what changed from the prior week. It surfaces only aggregates that cleared the
minimum-cohort gate, carries no per-student identity, and never ranks students.
"""

from __future__ import annotations

import datetime as dt

from ..contracts.schema import CurriculumContract
from ..data.audit import AuditLog
from ..data.repository import TenantRepository
from .aggregate import Aggregator, infer_cluster
from .review import InsightService
from .types import WeeklyBrief


class WeeklyBriefBuilder:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit
        self._agg = Aggregator(repo)
        self._insights = InsightService(repo, audit)

    def build(
        self,
        contract: CurriculumContract,
        course_id: str,
        window_start: dt.datetime,
        window_end: dt.datetime,
        *,
        prior_start: dt.datetime | None = None,
        prior_end: dt.datetime | None = None,
        review_minutes_per_insight: float = 1.5,
        actor_id: str = "system",
    ) -> WeeklyBrief:
        ws, we = window_start.isoformat(), window_end.isoformat()
        aggregates = self._agg.aggregate(contract, course_id, window_start, window_end)

        misconception, prereq, pressure = [], [], []
        out_of_scope_total = 0
        for agg in aggregates:
            out_of_scope_total += agg.out_of_scope
            cluster = infer_cluster(agg, contract)
            row = self._insights.persist_candidate(course_id, agg, cluster, ws, we)
            view = self._insights.to_view(row)
            if cluster.type == "prerequisite_gap":
                prereq.append(view)
            elif cluster.type == "full_solution_pressure":
                pressure.append(view)
            else:
                misconception.append(view)

        for bucket in (misconception, prereq, pressure):
            bucket.sort(key=lambda v: v.inferred.confidence, reverse=True)

        rising = self._rising_concepts(
            contract, course_id, aggregates, prior_start, prior_end
        )

        total = len(misconception) + len(prereq) + len(pressure)
        self._audit.record(
            action="insight.brief.generated",
            target_type="Course",
            target_id=course_id,
            actor_id=actor_id,
            detail=f"insights={total} window={ws}..{we}",
        )

        return WeeklyBrief(
            course_id=course_id,
            window_start=ws,
            window_end=we,
            misconception_clusters=misconception,
            prerequisite_gaps=prereq,
            full_solution_pressure=pressure,
            out_of_scope_count=out_of_scope_total,
            new_or_rising_concepts=rising,
            review_time_estimate_minutes=round(total * review_minutes_per_insight, 1),
        )

    def _rising_concepts(
        self, contract, course_id, current_aggs, prior_start, prior_end
    ) -> list[str]:
        if prior_start is None or prior_end is None:
            return [a.concept_name for a in current_aggs]
        prior = {
            a.concept_id: a.difficulty_students
            for a in self._agg.aggregate(contract, course_id, prior_start, prior_end)
        }
        rising = []
        for a in current_aggs:
            before = prior.get(a.concept_id, 0)
            if a.difficulty_students > before:
                rising.append(a.concept_name)
        return rising
