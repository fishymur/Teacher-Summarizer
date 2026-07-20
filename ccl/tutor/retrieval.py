"""Retrieval (section 8, pipeline step 5).

Keyword retrieval over the immutable source chunks created in Milestone 1,
filtered to the sources the policy decision allows. Vector retrieval
(pgvector + embeddings) replaces the scorer when that infrastructure lands; the
interface and the approved-source filter stay the same, so no rule changes.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

from sqlalchemy import select

from ..data.models import SourceChunk
from ..data.repository import TenantRepository
from ..providers.base import RetrievedChunk
from .embeddings import Embedder, cosine, make_embedder

_WORD = re.compile(r"[a-z0-9]+")


class Retriever(Protocol):
    """Fetches approved source chunks relevant to a query.

    Both KeywordRetriever and VectorRetriever satisfy this; the orchestrator and
    the approved-source filter don't care which is in use.
    """

    def retrieve(
        self, query: str, allowed_source_ids: list[str], k: int = 3
    ) -> list[RetrievedChunk]: ...


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


def _load_allowed_chunks(
    repo: TenantRepository, allowed_source_ids: list[str]
) -> list[SourceChunk]:
    """Chunks within the tenant whose owning material is approved by the policy.

    Identical approved-source filter used by both retrievers — this is the
    coherence guarantee, so it must not depend on which scorer is in use.
    """
    session = repo._session
    rows = session.scalars(
        select(SourceChunk)
        .where(SourceChunk.tenant_id == repo.tenant_id)
        .join(SourceChunk.material_version)
    ).all()
    allowed = set(allowed_source_ids)
    return [c for c in rows if c.material_version.material_id in allowed]


class VectorRetriever:
    """Semantic retrieval by cosine similarity over embedded chunks.

    Same interface and same approved-source filter as KeywordRetriever; only the
    scorer differs. Chunk embeddings are cached per process by chunk id, since
    the immutable SourceChunk text never changes. Persisting embeddings in a
    vector store is the deferred infrastructure step — with the deterministic
    local embedder, recompute-on-miss is cheap and needs no schema change.
    """

    def __init__(self, repo: TenantRepository, embedder: Embedder | None = None) -> None:
        self._repo = repo
        self._embedder = embedder or make_embedder()
        self._cache: dict[str, list[float]] = {}

    def _chunk_vector(self, chunk: SourceChunk) -> list[float]:
        vec = self._cache.get(chunk.id)
        if vec is None:
            vec = self._embedder.embed_one(chunk.text)
            self._cache[chunk.id] = vec
        return vec

    def retrieve(
        self, query: str, allowed_source_ids: list[str], k: int = 3
    ) -> list[RetrievedChunk]:
        if not allowed_source_ids:
            return []
        chunks = _load_allowed_chunks(self._repo, allowed_source_ids)
        if not chunks:
            return []

        q_vec = self._embedder.embed_one(query)
        scored: list[tuple[float, SourceChunk]] = [
            (cosine(q_vec, self._chunk_vector(c)), c) for c in chunks
        ]
        # Deterministic order: similarity desc, then anchor label asc.
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


def make_retriever(repo: TenantRepository) -> Retriever:
    """Select the retriever. Defaults to keyword so existing behavior is unchanged;
    set ``CCL_RETRIEVAL=vector`` to use embeddings-based semantic retrieval."""
    kind = os.environ.get("CCL_RETRIEVAL", "keyword").lower()
    if kind == "vector":
        return VectorRetriever(repo)
    if kind == "keyword":
        return KeywordRetriever(repo)
    raise ValueError(f"unknown CCL_RETRIEVAL={kind!r}; use 'keyword' or 'vector'")
