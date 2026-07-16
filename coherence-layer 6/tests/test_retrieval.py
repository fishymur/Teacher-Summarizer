"""Retrieval tests: only approved sources are returned, ranked by overlap."""

from ccl.data import AuditLog, MaterialService
from ccl.tutor import KeywordRetriever


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


def test_retrieval_only_returns_approved_sources(repo, audit):
    _seed_two_materials(repo, audit)
    r = KeywordRetriever(repo)
    hits = r.retrieve("vectors linear combination", ["material_notes_03"], k=5)
    assert hits, "expected at least one hit"
    assert all(h.source_id == "material_notes_03" for h in hits)


def test_retrieval_ranks_by_overlap(repo, audit):
    _seed_two_materials(repo, audit)
    r = KeywordRetriever(repo)
    hits = r.retrieve("vectors linear combination course method", ["material_notes_03"], k=2)
    # The p12 chunk overlaps more query terms than p13.
    assert hits[0].anchor == "p12"


def test_no_allowed_sources_returns_nothing(repo, audit):
    _seed_two_materials(repo, audit)
    r = KeywordRetriever(repo)
    assert r.retrieve("vectors", [], k=3) == []
