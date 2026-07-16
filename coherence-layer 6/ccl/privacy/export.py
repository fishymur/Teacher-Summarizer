"""Right-to-erasure and data export (sections 12 and 17).

``DeletionService`` erases one data subject's learning data — their sessions,
messages, and interaction events — while leaving de-identified aggregate
insights intact (they carry no identity, so erasing the subject does not require
destroying class-level patterns). The administrative audit record of the erasure
is itself retained (append-only), which is the standard, defensible posture.

``ExportService`` assembles a portable copy of a user's data for the subject or
an authorized administrator.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

from ..data.audit import AuditLog
from ..data.models import RoleAssignment
from ..data.privacy_models import DeletionRequest
from ..data.repository import TenantRepository
from ..data.tutor_models import InteractionEvent, TutorMessage, TutorSession


class DeletionService:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    def request_and_execute(
        self, *, subject_user_id: str, requested_by: str, scope: str = "learning_data"
    ) -> DeletionRequest:
        req = DeletionRequest(
            id=f"del_{uuid.uuid4().hex[:10]}",
            subject_user_id=subject_user_id,
            requested_by=requested_by,
            scope=scope,
            status="requested",
        )
        self._repo.add(req)
        self._repo.flush()
        self._audit.record(
            action="privacy.deletion.requested",
            target_type="DeletionRequest",
            target_id=req.id,
            actor_id=requested_by,
            detail=f"subject={subject_user_id} scope={scope}",
        )

        sessions = [
            s for s in self._repo.list(TutorSession)
            if s.student_id == subject_user_id
        ]
        session_ids = {s.id for s in sessions}
        for msg in self._repo.list(TutorMessage):
            if msg.session_id in session_ids:
                self._repo._session.delete(msg)
        for ev in self._repo.list(InteractionEvent):
            if ev.session_id in session_ids:
                self._repo._session.delete(ev)
        for s in sessions:
            self._repo._session.delete(s)

        req.status = "completed"
        req.completed_at = dt.datetime.now(dt.timezone.utc)
        self._repo.flush()
        self._audit.record(
            action="privacy.deletion.completed",
            target_type="DeletionRequest",
            target_id=req.id,
            actor_id=requested_by,
            detail=f"sessions={len(sessions)}",
        )
        return req


class ExportService:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    def export_user(self, user_id: str, actor_id: str) -> dict:
        sessions = [s for s in self._repo.list(TutorSession) if s.student_id == user_id]
        session_ids = {s.id for s in sessions}
        messages = [
            {
                "id": m.id, "session_id": m.session_id,
                "student_message": m.student_message, "response_text": m.response_text,
                "hint_level": m.hint_level, "outcome": m.outcome,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in self._repo.list(TutorMessage) if m.session_id in session_ids
        ]
        roles = [
            {"role": r.role, "course_id": r.course_id}
            for r in self._repo.list(RoleAssignment, user_id=user_id)
        ]
        self._audit.record(
            action="privacy.export",
            target_type="User",
            target_id=user_id,
            actor_id=actor_id,
        )
        return {
            "user_id": user_id,
            "sessions": [{"id": s.id, "course_id": s.course_id, "mode": s.mode} for s in sessions],
            "messages": messages,
            "roles": roles,
        }
