"""Workspace: the enforced access boundary (section 17).

A ``Workspace`` binds one authenticated principal and exposes the sensitive
operations. Every method checks a permission before delegating to a domain
service, so authorization lives at the boundary rather than being scattered
through domain logic. This is what an API request handler would construct
per-request; the domain services below it assume an already-authorized caller.

Note on transcripts (section 9.3): a student reading *their own* transcript is
the default and is not a privacy exception. Reading *someone else's* transcript
is an exception — it requires the escalated permission plus a documented reason
and is routed through ``TranscriptAccessService`` so it is audited.
"""

from __future__ import annotations

from ..data.audit import AuditLog
from ..data.insight_models import TeacherInsight
from ..data.models import Course
from ..data.repository import TenantRepository
from ..data.services import ContractService, MaterialService
from ..data.tutor_models import TutorMessage, TutorSession
from ..insights.review import (
    CorrectionService,
    InsightService,
    TranscriptAccessService,
)
from ..insights.types import CorrectionKind, ReviewAction
from ..privacy.config import RetentionConfig, load_config, save_config
from ..privacy.export import DeletionService, ExportService
from ..privacy.retention import PurgeReport, RetentionService
from ..governance.registry import ProviderRegistry
from .controller import AccessController
from .roles import Permission, Principal, Role
from .roleservice import RoleService


class Workspace:
    def __init__(
        self,
        principal: Principal,
        repo: TenantRepository,
        audit: AuditLog,
        controller: AccessController | None = None,
    ) -> None:
        if principal.tenant_id != repo.tenant_id:
            raise PermissionError("principal tenant does not match repository tenant")
        self._p = principal
        self._repo = repo
        self._audit = audit
        self._ac = controller or AccessController(audit)
        self._materials = MaterialService(repo, audit)
        self._contracts = ContractService(repo, audit)
        self._insights = InsightService(repo, audit)
        self._corrections = CorrectionService(repo, audit, self._contracts)
        self._transcripts = TranscriptAccessService(repo, audit)
        self._roles = RoleService(repo, audit)
        self._retention = RetentionService(repo, audit)
        self._deletion = DeletionService(repo, audit)
        self._export = ExportService(repo, audit)
        self._registry = ProviderRegistry(repo, audit)

    # --- courses & materials ------------------------------------------------

    def create_course(self, course_id: str, name: str) -> Course:
        self._ac.require(self._p, Permission.COURSE_CREATE, action="create_course")
        course = Course(id=course_id, name=name)
        self._repo.add(course)
        self._repo.flush()
        return course

    def import_material(self, *, material_id, course_id, title, kind, anchored_chunks):
        self._ac.require(self._p, Permission.MATERIAL_IMPORT, course_id, action="import_material")
        return self._materials.ingest(
            material_id=material_id, course_id=course_id, title=title, kind=kind,
            anchored_chunks=anchored_chunks, actor_id=self._p.user_id,
        )

    # --- contract lifecycle -------------------------------------------------

    def author_contract(self, contract):
        self._ac.require(self._p, Permission.CONTRACT_AUTHOR, contract.course_id, action="author_contract")
        return self._contracts.create_draft(contract)

    def validate_contract(self, contract_id: str, course_id: str):
        self._ac.require(self._p, Permission.CONTRACT_AUTHOR, course_id, action="validate_contract")
        return self._contracts.validate(contract_id)

    def approve_contract(self, contract_id: str, course_id: str):
        self._ac.require(self._p, Permission.CONTRACT_APPROVE, course_id, action="approve_contract")
        return self._contracts.approve(contract_id, approved_by=self._p.user_id)

    def publish_contract(self, contract_id: str, course_id: str):
        self._ac.require(self._p, Permission.CONTRACT_PUBLISH, course_id, action="publish_contract")
        return self._contracts.publish(contract_id, actor_id=self._p.user_id)

    # --- insights & corrections --------------------------------------------

    def view_insights(self, course_id: str) -> list[TeacherInsight]:
        self._ac.require(self._p, Permission.INSIGHT_VIEW, course_id, action="view_insights")
        return self._repo.list(TeacherInsight, course_id=course_id)

    def review_insight(self, insight_id: str, action: ReviewAction, course_id: str):
        self._ac.require(self._p, Permission.INSIGHT_REVIEW, course_id, action="review_insight")
        return self._insights.review(insight_id, action, actor_id=self._p.user_id)

    def submit_correction(self, *, course_id: str, target_type: str, target_id: str,
                          kind: CorrectionKind, resulting_action: str = "none",
                          base_contract_id: str | None = None,
                          evaluation_case: dict | None = None, note: str = ""):
        self._ac.require(self._p, Permission.CORRECTION_SUBMIT, course_id, action="submit_correction")
        return self._corrections.submit(
            course_id=course_id, target_type=target_type, target_id=target_id,
            kind=kind, created_by=self._p.user_id, note=note,
            resulting_action=resulting_action, base_contract_id=base_contract_id,
            evaluation_case=evaluation_case,
        )

    # --- transcripts --------------------------------------------------------

    def access_transcript(
        self, session_id: str, *, reason: str = "", justification: str = ""
    ) -> list[TutorMessage]:
        session = self._repo.get(TutorSession, session_id)
        if session is None:
            raise KeyError(session_id)
        if session.student_id == self._p.user_id:
            # Default case: a student reading their own transcript.
            self._ac.require(self._p, Permission.TRANSCRIPT_ACCESS_OWN, action="own_transcript")
            return self._repo.list(TutorMessage, session_id=session_id)
        # Exception case: reading someone else's transcript.
        self._ac.require(
            self._p, Permission.TRANSCRIPT_ACCESS_ESCALATED, action="escalated_transcript"
        )
        return self._transcripts.access(
            session_id=session_id, actor_id=self._p.user_id,
            reason=reason, justification=justification,
        )

    # --- role administration ------------------------------------------------

    def grant_role(self, user_id: str, role: Role, course_id: str | None):
        self._ac.require(self._p, Permission.ROLE_MANAGE, action="grant_role")
        return self._roles.grant(user_id, role, course_id, actor_id=self._p.user_id)

    def revoke_role(self, assignment_id: str):
        self._ac.require(self._p, Permission.ROLE_MANAGE, action="revoke_role")
        return self._roles.revoke(assignment_id, actor_id=self._p.user_id)

    # --- retention, deletion, export ----------------------------------------

    def configure_retention(self, config: RetentionConfig) -> None:
        self._ac.require(self._p, Permission.RETENTION_MANAGE, action="configure_retention")
        save_config(self._repo, config)

    def run_retention_purge(self, *, now=None) -> PurgeReport:
        self._ac.require(self._p, Permission.RETENTION_MANAGE, action="run_retention_purge")
        return self._retention.purge(now=now)

    def request_data_deletion(self, subject_user_id: str, *, scope: str = "learning_data"):
        # A data subject may erase their own data; anyone else needs admin rights.
        if subject_user_id != self._p.user_id:
            self._ac.require(self._p, Permission.RETENTION_MANAGE, action="delete_other_subject")
        return self._deletion.request_and_execute(
            subject_user_id=subject_user_id, requested_by=self._p.user_id, scope=scope
        )

    def export_user_data(self, subject_user_id: str) -> dict:
        if subject_user_id != self._p.user_id:
            self._ac.require(self._p, Permission.RETENTION_MANAGE, action="export_other_subject")
        return self._export.export_user(subject_user_id, actor_id=self._p.user_id)

    # --- provider registry --------------------------------------------------

    def register_provider(self, provider, *, approved_uses, subprocessors=None, version=""):
        self._ac.require(self._p, Permission.PROVIDER_MANAGE, action="register_provider")
        return self._registry.register(
            provider, approved_uses=approved_uses, subprocessors=subprocessors,
            version=version, actor_id=self._p.user_id,
        )

    def approve_provider(self, record_id: str, *, eval_run_id: str | None = None):
        self._ac.require(self._p, Permission.PROVIDER_MANAGE, action="approve_provider")
        return self._registry.approve(record_id, actor_id=self._p.user_id, eval_run_id=eval_run_id)

    def revoke_provider(self, record_id: str):
        self._ac.require(self._p, Permission.PROVIDER_MANAGE, action="revoke_provider")
        return self._registry.revoke(record_id, actor_id=self._p.user_id)

    def list_providers(self):
        self._ac.require(self._p, Permission.PROVIDER_MANAGE, action="list_providers")
        return self._registry.list_records()
