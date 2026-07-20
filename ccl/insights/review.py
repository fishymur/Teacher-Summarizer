"""Insight review, correction, and privacy-gated transcript access (section 9).

- ``InsightService`` persists candidate insights and records teacher review
  actions (confirm / incorrect / not-useful / merge).
- ``CorrectionService`` turns a teacher correction into either a *draft* contract
  version or a new evaluation case. It never mutates a published contract
  (section 9.4).
- ``TranscriptAccessService`` is the only path to raw student messages. It
  requires a documented reason and writes an append-only audit event
  (section 9.3, 17).
"""

from __future__ import annotations

import json
import uuid

from ..data.audit import AuditLog
from ..data.insight_models import EvaluationCaseRow, TeacherCorrection, TeacherInsight
from ..data.repository import TenantRepository
from ..data.services import ContractService
from ..data.tutor_models import TutorMessage
from .aggregate import ConceptAggregate, infer_cluster
from .types import (
    CorrectionKind,
    InferredCluster,
    InsightStatus,
    InsightView,
    ObservedFact,
    Recommendation,
    ReviewAction,
)

VALID_ACCESS_JUSTIFICATIONS = {
    "student_escalation",
    "documented_educational_need",
    "safety_workflow",
    "audited_support",
}


class InsightService:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    def persist_candidate(
        self,
        course_id: str,
        agg: ConceptAggregate,
        cluster: InferredCluster,
        window_start: str,
        window_end: str,
    ) -> TeacherInsight:
        observed = _observed_facts(agg, window_start, window_end)
        row = TeacherInsight(
            id=f"insight_{uuid.uuid4().hex[:12]}",
            course_id=course_id,
            window_start=window_start,
            window_end=window_end,
            type=cluster.type,
            concept_id=cluster.concept_id,
            summary=cluster.summary,
            confidence=cluster.confidence,
            sample_size=cluster.sample_size,
            supporting_event_ids=json.dumps(cluster.supporting_event_ids),
            source_refs=json.dumps(cluster.source_refs),
            observed_json=json.dumps([o.model_dump() for o in observed]),
            suggested_action=_suggested_action(cluster),
            status=InsightStatus.PENDING.value,
        )
        self._repo.add(row)
        self._repo.flush()
        return row

    def review(self, insight_id: str, action: ReviewAction, actor_id: str) -> TeacherInsight:
        row = self._repo.get(TeacherInsight, insight_id)
        if row is None:
            raise KeyError(insight_id)
        if action is ReviewAction.CONFIRM:
            row.status = InsightStatus.CONFIRMED.value
        elif action in (ReviewAction.INCORRECT, ReviewAction.NOT_USEFUL):
            row.status = InsightStatus.DISMISSED.value
        self._repo.flush()
        self._audit.record(
            action="insight.review",
            target_type="TeacherInsight",
            target_id=insight_id,
            actor_id=actor_id,
            detail=action.value,
        )
        return row

    def merge(self, primary_id: str, other_id: str, actor_id: str) -> None:
        other = self._repo.get(TeacherInsight, other_id)
        if other is None:
            raise KeyError(other_id)
        other.status = InsightStatus.MERGED.value
        self._repo.flush()
        self._audit.record(
            action="insight.merge",
            target_type="TeacherInsight",
            target_id=other_id,
            actor_id=actor_id,
            detail=f"merged_into={primary_id}",
        )

    def to_view(self, row: TeacherInsight) -> InsightView:
        observed = [ObservedFact(**o) for o in json.loads(row.observed_json)]
        inferred = InferredCluster(
            concept_id=row.concept_id,
            concept_name=row.concept_id,
            type=row.type,
            summary=row.summary,
            confidence=row.confidence,
            sample_size=row.sample_size,
            supporting_event_ids=json.loads(row.supporting_event_ids),
            source_refs=json.loads(row.source_refs),
        )
        rec = Recommendation(
            text=row.suggested_action,
            source_ref=(json.loads(row.source_refs) or [None])[0],
        )
        return InsightView(
            insight_id=row.id,
            observed=observed,
            inferred=inferred,
            recommended=rec,
            status=InsightStatus(row.status),
        )


class CorrectionService:
    def __init__(
        self, repo: TenantRepository, audit: AuditLog, contracts: ContractService
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._contracts = contracts

    def submit(
        self,
        *,
        course_id: str,
        target_type: str,
        target_id: str,
        kind: CorrectionKind,
        created_by: str,
        note: str = "",
        resulting_action: str = "none",
        base_contract_id: str | None = None,
        evaluation_case: dict | None = None,
    ) -> TeacherCorrection:
        resulting_ref: str | None = None

        if resulting_action == "draft_contract_change":
            if not base_contract_id:
                raise ValueError("base_contract_id required for a draft contract change")
            # Forks a NEW DRAFT version. The published contract is untouched.
            new_row = self._contracts.create_new_version(base_contract_id)
            resulting_ref = new_row.id
            self._audit.record(
                action="contract.correction.draft",
                target_type="CurriculumContract",
                target_id=new_row.id,
                actor_id=created_by,
                detail=f"from_correction on {target_type}:{target_id}",
            )
        elif resulting_action == "evaluation_case":
            if not evaluation_case:
                raise ValueError("evaluation_case payload required")
            case_id = f"evalcase_{uuid.uuid4().hex[:10]}"
            self._repo.add(
                EvaluationCaseRow(
                    id=case_id,
                    course_id=course_id,
                    contract_version_id=evaluation_case.get("contract_version_id", ""),
                    case_json=json.dumps(evaluation_case),
                    source="teacher_correction",
                )
            )
            self._repo.flush()
            resulting_ref = case_id
            self._audit.record(
                action="evaluation.case.create",
                target_type="EvaluationCase",
                target_id=case_id,
                actor_id=created_by,
            )

        correction = TeacherCorrection(
            id=f"corr_{uuid.uuid4().hex[:10]}",
            course_id=course_id,
            target_type=target_type,
            target_id=target_id,
            kind=kind.value,
            note=note,
            resulting_action=resulting_action,
            resulting_ref=resulting_ref,
            created_by=created_by,
        )
        self._repo.add(correction)
        self._repo.flush()
        self._audit.record(
            action="teacher.correction",
            target_type=target_type,
            target_id=target_id,
            actor_id=created_by,
            detail=kind.value,
        )
        return correction


class RawTranscriptAccessDenied(PermissionError):
    pass


class TranscriptAccessService:
    """The only path to raw student messages. Aggregates never come through here."""

    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    def access(
        self, *, session_id: str, actor_id: str, reason: str, justification: str
    ) -> list[TutorMessage]:
        if not reason.strip():
            raise RawTranscriptAccessDenied("a documented reason is required")
        if justification not in VALID_ACCESS_JUSTIFICATIONS:
            raise RawTranscriptAccessDenied(
                f"justification must be one of {sorted(VALID_ACCESS_JUSTIFICATIONS)}"
            )
        self._audit.record(
            action="privacy.raw_transcript_access",
            target_type="TutorSession",
            target_id=session_id,
            actor_id=actor_id,
            detail=f"{justification}: {reason}",
        )
        return self._repo.list(TutorMessage, session_id=session_id)


# --- helpers ----------------------------------------------------------------

def _observed_facts(agg: ConceptAggregate, ws: str, we: str) -> list[ObservedFact]:
    facts = [
        ObservedFact(
            metric="help_requests", value=agg.help_requests,
            denominator=agg.cohort_size, window_start=ws, window_end=we,
            scope=agg.concept_id,
        ),
        ObservedFact(
            metric="students_with_difficulty", value=agg.difficulty_students,
            denominator=agg.cohort_size, window_start=ws, window_end=we,
            scope=agg.concept_id,
        ),
    ]
    if agg.full_solution_requests:
        facts.append(
            ObservedFact(
                metric="full_solution_requests", value=agg.full_solution_requests,
                denominator=agg.help_requests or agg.cohort_size,
                window_start=ws, window_end=we, scope=agg.concept_id,
            )
        )
    return facts


def _suggested_action(cluster: InferredCluster) -> str:
    ref = f" See {cluster.source_refs[0]}." if cluster.source_refs else ""
    if cluster.type == "prerequisite_gap":
        return f"Briefly reteach the prerequisite '{cluster.concept_name}' before the next unit.{ref}"
    if cluster.type == "full_solution_pressure":
        return (
            f"Consider a short in-class worked example for '{cluster.concept_name}' so "
            f"students rely less on requesting full solutions.{ref}"
        )
    return f"Revisit '{cluster.concept_name}' with a targeted retrieval question.{ref}"
