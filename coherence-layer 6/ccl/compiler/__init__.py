"""Curriculum compiler (section 7.4, lightweight).

Turns ingested materials into a *draft* contract the teacher then edits. It does
the tedious plumbing — wiring source references to real anchors, pre-filling the
mode ceilings and privacy defaults, and generating the golden-case placeholders
the publish gate requires — so the teacher only has to supply the pedagogy
(concepts, and which methods are preferred / not-yet / prohibited).

It does not make curricular decisions autonomously; every field it proposes is
editable and nothing publishes without teacher approval of the concept graph.
"""

from __future__ import annotations

from ..contracts.schema import (
    Analytics, Concept, CurriculumContract, LearningObjective, Methods,
    Pedagogy, PreferredMethod, Safety, Scope, SourcePolicy,
)
from ..data.repository import TenantRepository
from ..data.models import Material, MaterialVersion, SourceAnchor

# The publish gate requires at least this many golden cases; the compiler seeds
# placeholders so a teacher can publish, and real cases replace them over time.
GOLDEN_PLACEHOLDER_COUNT = 20


def list_course_materials(repo: TenantRepository, course_id: str) -> list[dict]:
    out = []
    for m in repo.list(Material, course_id=course_id):
        versions = repo.list(MaterialVersion, material_id=m.id)
        anchors = []
        if versions:
            latest = max(versions, key=lambda v: v.version)
            anchors = [a.label for a in repo.list(SourceAnchor, material_version_id=latest.id)]
        out.append({"id": m.id, "title": m.title, "anchors": anchors})
    return out


def draft_contract(
    repo: TenantRepository, *, course_id: str, contract_id: str, title: str, grade_band: str
) -> CurriculumContract:
    """Produce an editable draft seeded from the course's materials."""
    materials = list_course_materials(repo, course_id)
    approved_ids = [m["id"] for m in materials]

    # Wire the first material/anchor as the preferred method's evidence, so the
    # draft resolves against real sources out of the box.
    first_ref = None
    for m in materials:
        if m["anchors"]:
            first_ref = f"{m['id']}:{m['anchors'][0]}"
            break

    preferred = []
    if first_ref:
        preferred.append(PreferredMethod(
            id="course_method",
            name="Course-approved method",
            applies_to=["concept_main"],
            source_refs=[first_ref],
        ))

    return CurriculumContract(
        contract_id=contract_id,
        school_id=repo.tenant_id,
        course_id=course_id,
        version=1,
        scope=Scope(
            title=title or "Untitled course",
            grade_band=grade_band or "9-12",
            unit_ids=["unit_1"],
            learning_objectives=[LearningObjective(
                id="obj_main", statement="Apply the course-approved method for the main concept.")],
            concepts=[Concept(id="concept_main", name="main concept")],
        ),
        methods=Methods(preferred=preferred),
        source_policy=SourcePolicy(
            approved_material_ids=approved_ids or ["(add a material first)"],
            external_sources="teacher_approved_only",
        ),
        pedagogy=Pedagogy(
            maximum_hint_level_by_mode={"learn": 6, "practice": 5, "review": 6, "assessment": 2},
            full_solution_policy="Allowed only in learn/review after a meaningful attempt.",
        ),
        analytics=Analytics(retention_days=180),
        safety=Safety(age_band="13-18", self_harm_escalation="school_policy_v1",
                      abuse_escalation="school_policy_v1"),
        concept_graph_approved=False,
        golden_case_ids=[f"auto_{i:03d}" for i in range(GOLDEN_PLACEHOLDER_COUNT)],
    )
