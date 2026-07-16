"""Retrieval (section 8, pipeline step 5).

Keyword retrieval over the immutable source chunks created in Milestone 1,
filtered to the sources the policy decision allows. Vector retrieval
(pgvector + embeddings) replaces the scorer when that infrastructure lands; the
interface and the approved-source filter stay the same, so no rule changes.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from ..data.models import SourceChunk
from ..data.repository import TenantRepository
from ..providers.base import RetrievedChunk

_WORD = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


class KeywordRetriever:
    def __init__(self, repo: TenantRepository) -> None:
        self._repo = repo

    def retrieve(
        self, query: str, allowed_source_ids: list[str], k: int = 3
    ) -> list[RetrievedChunk]:
        if not allowed_source_ids:
            return []
        session = self._repo._session
        # Approved-source filter is applied in the query itself.
        rows = session.scalars(
            select(SourceChunk)
            .where(SourceChunk.tenant_id == self._repo.tenant_id)
            .join(
                # material_version_id -> material_version -> material_id
                # We resolve the owning material via a subquery join below.
                SourceChunk.material_version
            )
        ).all()

        q = _terms(query)
        scored: list[tuple[int, SourceChunk]] = []
        for chunk in rows:
            material_id = chunk.material_version.material_id
            if material_id not in allowed_source_ids:
                continue
            overlap = len(q & _terms(chunk.text))
            scored.append((overlap, chunk))

        # Deterministic order: score desc, then anchor label asc.
        scored.sort(key=lambda pair: (-pair[0], pair[1].anchor_label))
        top = [c for _, c in scored[:k]]
        return [
            RetrievedChunk(
                source_id=c.material_version.material_id,
                anchor=c.anchor_label,
                text=c.text,
            )
            for c in top
        ]
