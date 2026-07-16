"""Acceptance: material import produces stable anchors and immutable chunks.

Maps to MVP capability #2 (page/slide anchors) and section 11 invariant
"source chunks are immutable within a material version".
"""

import pytest

from ccl.data.audit import DBAnchorResolver
from ccl.data.models import ImmutableRowError, SourceAnchor, SourceChunk


def test_ingest_creates_anchors_and_chunks(repo, seeded_material):
    anchors = repo.list(SourceAnchor)
    chunks = repo.list(SourceChunk)
    assert {a.label for a in anchors} == {"p12", "p13", "p14"}
    assert len(chunks) == 3
    assert all(c.text for c in chunks)


def test_source_chunks_are_immutable(session, repo, seeded_material):
    chunk = repo.list(SourceChunk)[0]
    chunk.text = "tampered"
    with pytest.raises(ImmutableRowError):
        session.flush()


def test_anchor_resolver_resolves_only_approved_refs(repo, seeded_material):
    resolver = DBAnchorResolver(repo)
    approved = ["material_notes_03"]
    assert resolver.resolves("material_notes_03:p12-p14", approved) is True
    # Unknown anchor on a known material does not resolve.
    assert resolver.resolves("material_notes_03:p99", approved) is False
    # Material not in the approved list does not resolve.
    assert resolver.resolves("material_notes_03:p12", []) is False
    # A ref with no anchor does not resolve.
    assert resolver.resolves("material_notes_03:", approved) is False
