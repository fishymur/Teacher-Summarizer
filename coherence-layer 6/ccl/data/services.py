"""Orchestration services.

These tie the pure schema/validation/policy packages to the persistence layer
and the audit log. They are the functions the reference API endpoints in
section 12 would call.
"""

from __future__ import annotations

import hashlib
import uuid

from ..contracts.lifecycle import assert_transition
from ..contracts.schema import ContractStatus, CurriculumContract
from ..validation.validator import ValidationResult, validate_for_publish
from .audit import AuditLog, DBAnchorResolver
from .models import (
    CurriculumContractRow,
    ContractApproval,
    Material,
    MaterialVersion,
    SourceAnchor,
    SourceChunk,
)
from .repository import TenantRepository


class MaterialService:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    def ingest(
        self,
        *,
        material_id: str,
        course_id: str,
        title: str,
        kind: str,
        anchored_chunks: list[tuple[str, str, str]],
        actor_id: str | None = None,
    ) -> MaterialVersion:
        """Ingest a material as version 1 with stable anchors and chunks.

        ``anchored_chunks`` is a list of ``(anchor_label, anchor_kind, text)``.
        Real ingestion parses a PDF/slide deck; here the parsed output is passed
        in so the milestone stays free of a PDF dependency.
        """
        material = self._repo.get(Material, material_id)
        if material is None:
            material = Material(
                id=material_id, course_id=course_id, title=title, kind=kind
            )
            self._repo.add(material)

        checksum = hashlib.sha256(
            "".join(text for _, _, text in anchored_chunks).encode("utf-8")
        ).hexdigest()
        version = MaterialVersion(
            id=f"mv_{uuid.uuid4().hex[:12]}",
            material_id=material_id,
            version=1,
            checksum=checksum,
        )
        self._repo.add(version)
        self._repo.flush()

        for label, kind_, text in anchored_chunks:
            self._repo.add(
                SourceAnchor(
                    id=f"anc_{uuid.uuid4().hex[:10]}",
                    material_version_id=version.id,
                    label=label,
                    kind=kind_,
                )
            )
            self._repo.add(
                SourceChunk(
                    id=f"chk_{uuid.uuid4().hex[:10]}",
                    material_version_id=version.id,
                    anchor_label=label,
                    text=text,
                )
            )
        self._repo.flush()

        self._audit.record(
            action="material.import",
            target_type="MaterialVersion",
            target_id=version.id,
            actor_id=actor_id,
            detail=f"material={material_id} chunks={len(anchored_chunks)}",
        )
        return version


class ContractPublishError(RuntimeError):
    pass


class ContractService:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    # --- persistence helpers ------------------------------------------------

    def _load(self, contract_id: str) -> CurriculumContractRow:
        row = self._repo.get(CurriculumContractRow, contract_id)
        if row is None:
            raise KeyError(f"contract {contract_id!r} not found in this tenant")
        return row

    @staticmethod
    def _to_model(row: CurriculumContractRow) -> CurriculumContract:
        model = CurriculumContract.model_validate_json(row.document_json)
        # The row status is authoritative for lifecycle.
        return model.model_copy(update={"status": ContractStatus(row.status)})

    def _save_document(self, row: CurriculumContractRow, model: CurriculumContract) -> None:
        row.document_json = model.model_dump_json()

    # --- lifecycle ----------------------------------------------------------

    def create_draft(self, contract: CurriculumContract) -> CurriculumContractRow:
        draft = contract.model_copy(update={"status": ContractStatus.DRAFT})
        row = CurriculumContractRow(
            id=draft.contract_id,
            course_id=draft.course_id,
            version=draft.version,
            status=ContractStatus.DRAFT.value,
            document_json=draft.model_dump_json(),
        )
        self._repo.add(row)
        self._repo.flush()
        return row

    def validate(self, contract_id: str) -> ValidationResult:
        row = self._load(contract_id)
        model = self._to_model(row)
        resolver = DBAnchorResolver(self._repo)
        result = validate_for_publish(model, resolver)
        # draft -> validating regardless of outcome; failing sends it back.
        current = ContractStatus(row.status)
        if current == ContractStatus.DRAFT:
            assert_transition(current, ContractStatus.VALIDATING)
            row.status = ContractStatus.VALIDATING.value
        if not result.is_valid and ContractStatus(row.status) == ContractStatus.VALIDATING:
            assert_transition(ContractStatus.VALIDATING, ContractStatus.DRAFT)
            row.status = ContractStatus.DRAFT.value
        self._repo.flush()
        return result

    def approve(self, contract_id: str, approved_by: str) -> None:
        row = self._load(contract_id)
        model = self._to_model(row)
        resolver = DBAnchorResolver(self._repo)
        result = validate_for_publish(model, resolver)
        if not result.is_valid:
            raise ContractPublishError(
                f"cannot approve invalid contract: {sorted(c.value for c in result.codes)}"
            )
        current = ContractStatus(row.status)
        if current == ContractStatus.DRAFT:
            row.status = ContractStatus.VALIDATING.value
            current = ContractStatus.VALIDATING
        assert_transition(current, ContractStatus.APPROVED)
        row.status = ContractStatus.APPROVED.value
        self._repo.add(
            ContractApproval(
                id=f"appr_{uuid.uuid4().hex[:10]}",
                contract_id=contract_id,
                approved_by=approved_by,
            )
        )
        self._repo.flush()
        self._audit.record(
            action="contract.approve",
            target_type="CurriculumContract",
            target_id=contract_id,
            actor_id=approved_by,
        )

    def publish(self, contract_id: str, actor_id: str | None = None) -> None:
        row = self._load(contract_id)
        assert_transition(ContractStatus(row.status), ContractStatus.PUBLISHED)

        # Supersede any currently-published contract for the same course so only
        # one version governs at a time.
        for other in self._repo.list(
            CurriculumContractRow, course_id=row.course_id, status=ContractStatus.PUBLISHED.value
        ):
            assert_transition(ContractStatus.PUBLISHED, ContractStatus.SUPERSEDED)
            other.status = ContractStatus.SUPERSEDED.value
            self._audit.record(
                action="contract.supersede",
                target_type="CurriculumContract",
                target_id=other.id,
                actor_id=actor_id,
            )

        row.status = ContractStatus.PUBLISHED.value
        self._repo.flush()
        self._audit.record(
            action="contract.publish",
            target_type="CurriculumContract",
            target_id=contract_id,
            actor_id=actor_id,
        )

    def create_new_version(self, base_contract_id: str) -> CurriculumContractRow:
        """A change never mutates a published version. It forks a new draft."""
        base = self._load(base_contract_id)
        model = self._to_model(base)
        new_version = base.version + 1
        new_model = model.model_copy(
            update={
                "contract_id": f"{model.course_id}_v{new_version}",
                "version": new_version,
                "status": ContractStatus.DRAFT,
            }
        )
        return self.create_draft(new_model)

    def get_published(self, course_id: str) -> CurriculumContract | None:
        rows = self._repo.list(
            CurriculumContractRow, course_id=course_id, status=ContractStatus.PUBLISHED.value
        )
        if not rows:
            return None
        return self._to_model(rows[0])
