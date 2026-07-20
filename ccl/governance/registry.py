"""Provider registry service (sections 15, 17, 10.5).

Registers a provider from its self-declared data policy and capabilities,
governs approval, and gates runtime usage. Two rules are enforced:

- **Training must be disabled to approve.** A provider that reserves the right to
  train on student data cannot be approved (section 17: student data excluded
  from provider training by default).
- **Fail closed at runtime.** ``ensure_usable`` raises unless the model is
  registered, approved, and still has training disabled. The orchestrator calls
  this before it ever hands data to a model, so an unvetted model change cannot
  quietly reach students.

Section 10.5 asks that a model change trigger the local evaluation suite before
deployment; ``approve`` records the ``eval_run_id`` that cleared Gate 3 so the
approval is tied to evidence.
"""

from __future__ import annotations

import json
import uuid

from ..data.audit import AuditLog
from ..data.provider_models import ProviderRecord
from ..data.repository import TenantRepository
from ..providers.base import LLMProvider


class ProviderNotApproved(RuntimeError):
    """Raised at runtime when a model is not registered/approved for use."""


class ApprovalRefused(ValueError):
    """Raised when a provider cannot be approved (e.g. training not disabled)."""


class ProviderRegistry:
    def __init__(self, repo: TenantRepository, audit: AuditLog) -> None:
        self._repo = repo
        self._audit = audit

    def register(
        self,
        provider: LLMProvider,
        *,
        approved_uses: list[str],
        subprocessors: list[str] | None = None,
        version: str = "",
        actor_id: str,
    ) -> ProviderRecord:
        dp = provider.data_policy()
        cap = provider.capabilities()
        record = ProviderRecord(
            id=f"prov_{uuid.uuid4().hex[:10]}",
            provider=dp.provider,
            model_id=cap.model_id,
            version=version,
            region=dp.region,
            retention_days=dp.retention_days,
            training_disabled=dp.training_disabled,
            subprocessors_json=json.dumps(subprocessors or []),
            approved_uses_json=json.dumps(approved_uses),
            status="registered",
        )
        self._repo.add(record)
        self._repo.flush()
        self._audit.record(
            action="provider.register",
            target_type="ProviderRecord",
            target_id=record.id,
            actor_id=actor_id,
            detail=f"{dp.provider}:{cap.model_id} region={dp.region} training_disabled={dp.training_disabled}",
        )
        return record

    def approve(self, record_id: str, actor_id: str, *, eval_run_id: str | None = None) -> ProviderRecord:
        record = self._repo.get(ProviderRecord, record_id)
        if record is None:
            raise KeyError(record_id)
        if not record.training_disabled:
            raise ApprovalRefused(
                f"cannot approve {record.model_id!r}: provider training is not disabled"
            )
        record.status = "approved"
        record.eval_run_id = eval_run_id
        self._repo.flush()
        self._audit.record(
            action="provider.approve",
            target_type="ProviderRecord",
            target_id=record_id,
            actor_id=actor_id,
            detail=f"eval_run={eval_run_id}",
        )
        return record

    def revoke(self, record_id: str, actor_id: str) -> None:
        record = self._repo.get(ProviderRecord, record_id)
        if record is None:
            raise KeyError(record_id)
        record.status = "revoked"
        self._repo.flush()
        self._audit.record(
            action="provider.revoke",
            target_type="ProviderRecord",
            target_id=record_id,
            actor_id=actor_id,
        )

    def ensure_usable(self, model_id: str) -> ProviderRecord:
        for r in self._repo.list(ProviderRecord, model_id=model_id):
            if r.status == "approved" and r.training_disabled:
                return r
        raise ProviderNotApproved(
            f"model {model_id!r} is not an approved, training-disabled provider"
        )

    def list_records(self) -> list[ProviderRecord]:
        return self._repo.list(ProviderRecord)
