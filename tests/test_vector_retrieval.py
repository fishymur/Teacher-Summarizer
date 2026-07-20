"""Vector retrieval: same guarantees as keyword, cosine-ranked.

The approved-source filter must hold regardless of scorer (the coherence
guarantee), ranking is by cosine similarity, and the selector honors
CCL_RETRIEVAL. The local embedder is deterministic and offline, so these are
stable without any external service.
"""

import pytest

from ccl.data import MaterialService
from ccl.tutor import KeywordRetriever, VectorRetriever, make_retriever
from ccl.tutor.embeddings import LocalHashingEmbedder, cosine, make_embedder


def _seed_two_materials(repo, audit):
    svc = MaterialService(repo, audit)
    svc.ingest(
        material_id="material_notes_03", course_id="math_demo", title="Notes", kind="pdf",
        anchored_chunks=[
            ("p12", "page", "vectors linear combination course method"),
            ("p13", "page", "unrelated content about triangles"),
        ],
    )
    svc.ingest(
        material_id="material_external", course_id="math_demo", title="Ext", kind="pdf",
        anchored_chunks=[("p1", "page", "vectors linear combination external source")],
    )


def test_vector_retrieval_only_returns_approved_sources(repo, audit):
    _seed_two_materials(repo, audit)
    r = VectorRetriever(repo)
    hits = r.retrieve("vectors linear combination", ["material_notes_03"], k=5)
    assert hits, "expected at least one hit"
    assert all(h.source_id == "material_notes_03" for h in hits)


def test_vector_retrieval_ranks_by_similarity(repo, audit):
    _seed_two_materials(repo, audit)
    r = VectorRetriever(repo)
    hits = r.retrieve("vectors linear combination course method", ["material_notes_03"], k=2)
    # p12 is far closer to the query than the triangles chunk p13.
    assert hits[0].anchor == "p12"


def test_vector_no_allowed_sources_returns_nothing(repo, audit):
    _seed_two_materials(repo, audit)
    r = VectorRetriever(repo)
    assert r.retrieve("vectors", [], k=3) == []


def test_embedder_is_deterministic_and_normalized():
    e = LocalHashingEmbedder()
    v1 = e.embed_one("vectors linear combination")
    v2 = e.embed_one("vectors linear combination")
    assert v1 == v2  # deterministic across calls
    # L2-normalized (self cosine == 1), empty text degrades gracefully to 0.
    assert cosine(v1, v1) == pytest.approx(1.0)
    assert cosine(e.embed_one(""), e.embed_one("")) == pytest.approx(0.0)


def test_shared_vocabulary_scores_higher_than_disjoint():
    e = LocalHashingEmbedder()
    q = e.embed_one("vectors linear combination")
    close = e.embed_one("linear combination of vectors")
    far = e.embed_one("triangles and circles geometry")
    assert cosine(q, close) > cosine(q, far)


def test_make_retriever_selects_by_env(repo, monkeypatch):
    monkeypatch.setenv("CCL_RETRIEVAL", "vector")
    assert isinstance(make_retriever(repo), VectorRetriever)
    monkeypatch.setenv("CCL_RETRIEVAL", "keyword")
    assert isinstance(make_retriever(repo), KeywordRetriever)
    monkeypatch.delenv("CCL_RETRIEVAL", raising=False)
    assert isinstance(make_retriever(repo), KeywordRetriever)  # default


def test_make_retriever_rejects_unknown(repo, monkeypatch):
    monkeypatch.setenv("CCL_RETRIEVAL", "bogus")
    with pytest.raises(ValueError):
        make_retriever(repo)


def test_make_embedder_rejects_unknown():
    with pytest.raises(ValueError):
        make_embedder("bogus")
