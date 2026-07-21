"""Regression: material ingest strips NUL (0x00) bytes.

PostgreSQL text/varchar columns reject NUL bytes (the DataError seen when
pasting PDF-extracted notes into the teacher Studio); SQLite silently accepted
them. The fix strips NULs at the single ingestion chokepoint (MaterialService
.ingest), so every upload path (paste, text file, text/scanned PDF) is covered.
"""

from ccl.data.services import MaterialService
from ccl.data.models import SourceChunk


def test_ingest_strips_nul_bytes(repo, audit):
    materials = MaterialService(repo, audit)
    version = materials.ingest(
        material_id="nul_probe",
        course_id="math_demo",  # seeded by the `repo` fixture
        title="NUL probe",
        kind="doc",
        anchored_chunks=[
            ("p1", "page", "before\x00after"),
            ("p2", "page", "\x00leading"),
            ("p3", "page", "clean text"),
        ],
        actor_id="user_teacher_123",
    )

    stored = {
        c.anchor_label: c.text
        for c in repo.list(SourceChunk)
        if c.material_version_id == version.id
    }
    assert stored["p1"] == "beforeafter"
    assert stored["p2"] == "leading"
    assert stored["p3"] == "clean text"
    assert all("\x00" not in t for t in stored.values())
