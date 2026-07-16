"""Aggregation engine (sections 9.3 and 10).

Turns raw interaction events into evidence-backed candidate clusters. The two
non-negotiable properties:

- **Minimum cohort suppression.** A concept touched by fewer than ``MIN_COHORT``
  distinct students produces *nothing*. This is the privacy default; a small
  cohort cannot be surfaced even if every student in it struggled.
- **No identity leaves this layer.** Student ids are used only to count distinct
  people and are never returned. The output carries counts, not students.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

from ..contracts.schema import CurriculumContract
from ..data.repository import TenantRepository
from ..data.tutor_models import InteractionEvent, TutorSession
from .types import MIN_COHORT, InferredCluster

HELP_EVENT = "tutor_help_requested"
DIFFICULTY_EVENTS = {
    "full_solution_requested",
    "high_support_reached",
    "fallback_delivered",
    "out_of_scope_question",
}


@dataclass
class ConceptAggregate:
    concept_id: str
    concept_name: str
    cohort_size: int
    difficulty_students: int
    help_requests: int
    full_solution_requests: int
    high_support: int
    out_of_scope: int
    supporting_event_ids: list[str] = field(default_factory=list)
    is_prerequisite: bool = False


class Aggregator:
    def __init__(self, repo: TenantRepository) -> None:
        self._repo = repo

    def aggregate(
        self,
        contract: CurriculumContract,
        course_id: str,
        window_start: dt.datetime,
        window_end: dt.datetime,
    ) -> list[ConceptAggregate]:
        events = [
            e
            for e in self._repo.list(InteractionEvent, course_id=course_id)
            if _in_window(e.created_at, window_start, window_end)
        ]
        # session_id -> student_id, used only for distinct counts.
        student_of = {
            s.id: s.student_id
            for s in self._repo.list(TutorSession, course_id=course_id)
        }
        names = {c.id: c.name for c in contract.scope.concepts}
        prereqs = set(contract.scope.prerequisite_assumptions)

        # Bucket events by concept.
        by_concept: dict[str, list[InteractionEvent]] = {}
        for e in events:
            if not e.concept_id:
                continue
            by_concept.setdefault(e.concept_id, []).append(e)

        results: list[ConceptAggregate] = []
        for concept_id, evs in by_concept.items():
            help_students = {
                student_of.get(e.session_id)
                for e in evs
                if e.type == HELP_EVENT
            }
            help_students.discard(None)
            cohort = len(help_students)

            # Minimum-cohort suppression: privacy default.
            if cohort < MIN_COHORT:
                continue

            diff_students = {
                student_of.get(e.session_id)
                for e in evs
                if e.type in DIFFICULTY_EVENTS
            }
            diff_students.discard(None)

            results.append(
                ConceptAggregate(
                    concept_id=concept_id,
                    concept_name=names.get(concept_id, concept_id),
                    cohort_size=cohort,
                    difficulty_students=len(diff_students),
                    help_requests=sum(1 for e in evs if e.type == HELP_EVENT),
                    full_solution_requests=sum(
                        1 for e in evs if e.type == "full_solution_requested"
                    ),
                    high_support=sum(
                        1 for e in evs if e.type == "high_support_reached"
                    ),
                    out_of_scope=sum(
                        1 for e in evs if e.type == "out_of_scope_question"
                    ),
                    supporting_event_ids=[
                        e.id for e in evs if e.type in DIFFICULTY_EVENTS
                    ],
                    is_prerequisite=concept_id in prereqs,
                )
            )
        return results


def infer_cluster(
    agg: ConceptAggregate, contract: CurriculumContract
) -> InferredCluster:
    """Build an evidence-backed inference from an aggregate.

    Confidence is the share of the cohort that showed a difficulty signal — a
    deterministic, inspectable proportion, not a black-box score. Counter-
    evidence is the rest of the cohort that asked for help without struggling.
    """
    cohort = max(agg.cohort_size, 1)
    confidence = round(agg.difficulty_students / cohort, 2)
    counter = max(agg.cohort_size - agg.difficulty_students, 0)

    if agg.is_prerequisite:
        itype = "prerequisite_gap"
        summary = (
            f"Repeated difficulty on prerequisite '{agg.concept_name}' suggests a "
            "gap carried in from earlier material."
        )
    elif agg.full_solution_requests >= agg.help_requests * 0.5 and agg.help_requests:
        itype = "full_solution_pressure"
        summary = (
            f"Students frequently ask for the full solution on '{agg.concept_name}' "
            "rather than working through hints."
        )
    else:
        itype = "misconception_cluster"
        summary = (
            f"A recurring difficulty pattern appears on '{agg.concept_name}'."
        )

    # Attach a source ref from the preferred method for this concept, if any.
    source_refs: list[str] = []
    for m in contract.methods.preferred:
        if agg.concept_id in m.applies_to:
            source_refs = list(m.source_refs)
            break

    return InferredCluster(
        concept_id=agg.concept_id,
        concept_name=agg.concept_name,
        type=itype,
        summary=summary,
        confidence=confidence,
        sample_size=agg.cohort_size,
        counterevidence_count=counter,
        supporting_event_ids=agg.supporting_event_ids,
        source_refs=source_refs,
    )


def _in_window(ts: dt.datetime, start: dt.datetime, end: dt.datetime) -> bool:
    # Normalise naive/aware mismatch conservatively.
    if ts.tzinfo is None and start.tzinfo is not None:
        ts = ts.replace(tzinfo=start.tzinfo)
    return start <= ts < end
