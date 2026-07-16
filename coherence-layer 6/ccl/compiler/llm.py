"""LLM-backed curriculum compiler (section 7.4).

Reads the course's materials with a live model and proposes a first-draft
contract: the main concept, the preferred method, an optional method to hold
back and one to prohibit, learning objectives, and a set of evaluation
questions. The teacher then edits and approves it — the compiler never
publishes on its own.

Falls back to the heuristic seed (`draft_contract`) whenever no completion-capable
provider is available (e.g. the offline stub), so the Studio always works.
"""

from __future__ import annotations

import json
import uuid

from ..contracts.schema import (
    Analytics, Concept, CurriculumContract, LearningObjective, Methods,
    NotYetIntroducedMethod, Pedagogy, PreferredMethod, ProhibitedMethod, Safety, Scope, SourcePolicy,
)
from ..data.insight_models import EvaluationCaseRow
from ..data.models import Material, MaterialVersion, SourceAnchor, SourceChunk
from ..data.repository import TenantRepository
from . import GOLDEN_PLACEHOLDER_COUNT, draft_contract, list_course_materials

_SYSTEM = (
    "You are a curriculum compiler for a specific school course. You read the "
    "teacher's own materials and propose a DRAFT set of teaching rules for a "
    "tutor to follow. You do not invent facts beyond the materials. Respond with "
    "ONLY a JSON object, no prose or code fences, of this exact shape:\n"
    '{"concept": {"name": str},'
    ' "preferred_method": {"name": str},'
    ' "not_yet_method": {"name": str, "until_unit": str, "boundary": str} | null,'
    ' "prohibited_method": {"name": str} | null,'
    ' "objectives": [str, ...],'
    ' "questions": [str, ...]}\n'
    "Rules: 'concept' is the single main topic of the materials. 'preferred_method' "
    "is the approach the materials actually teach. 'not_yet_method' is a common "
    "alternative approach students might reach for that this material does NOT use "
    "yet (or null if none is obvious); 'boundary' is one sentence the tutor says to "
    "redirect a student who asks for it. 'prohibited_method' is an approach that "
    "would be wrong here (or null). Give 6-10 short 'objectives' and 20 short "
    "'questions' a student might ask, mixing ordinary questions with a few that try "
    "to get the answer handed over or to use a not-yet/prohibited method."
)


def _corpus(repo: TenantRepository, course_id: str, limit_chars: int = 6000) -> str:
    parts: list[str] = []
    total = 0
    for m in repo.list(Material, course_id=course_id):
        versions = repo.list(MaterialVersion, material_id=m.id)
        if not versions:
            continue
        latest = max(versions, key=lambda v: v.version)
        for ch in repo.list(SourceChunk, material_version_id=latest.id):
            snippet = f"[{m.title} {ch.anchor_label}] {ch.text}"
            parts.append(snippet)
            total += len(snippet)
            if total >= limit_chars:
                return "\n".join(parts)
    return "\n".join(parts)


def _first_anchor_ref(repo: TenantRepository, course_id: str) -> str | None:
    for m in repo.list(Material, course_id=course_id):
        versions = repo.list(MaterialVersion, material_id=m.id)
        if not versions:
            continue
        latest = max(versions, key=lambda v: v.version)
        anchors = repo.list(SourceAnchor, material_version_id=latest.id)
        if anchors:
            return f"{m.id}:{anchors[0].label}"
    return None


def _parse(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _slug(s: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in (s or "").lower()).strip("_")
    return out or "x"


def llm_draft_contract(
    provider,
    repo: TenantRepository,
    *,
    course_id: str,
    contract_id: str,
    title: str,
    grade_band: str,
) -> tuple[CurriculumContract, bool]:
    """Return (draft_contract, used_llm). Falls back to the heuristic seed."""
    complete = getattr(provider, "complete", None)
    corpus = _corpus(repo, course_id)
    if complete is None or not corpus.strip():
        return draft_contract(repo, course_id=course_id, contract_id=contract_id,
                              title=title, grade_band=grade_band), False

    raw = complete(_SYSTEM, f"Course title: {title}\n\nMaterials:\n{corpus}")
    parsed = _parse(raw)
    if not parsed or "concept" not in parsed:
        return draft_contract(repo, course_id=course_id, contract_id=contract_id,
                              title=title, grade_band=grade_band), False

    concept_name = (parsed.get("concept") or {}).get("name", "main concept")
    concept_id = f"concept_{_slug(concept_name)}"
    ref = _first_anchor_ref(repo, course_id)

    preferred = [PreferredMethod(
        id="course_method",
        name=(parsed.get("preferred_method") or {}).get("name", "Course-approved method"),
        applies_to=[concept_id],
        source_refs=[ref] if ref else [],
    )]

    not_yet = []
    ny = parsed.get("not_yet_method")
    if isinstance(ny, dict) and ny.get("name"):
        not_yet.append(NotYetIntroducedMethod(
            id=f"not_yet_{_slug(ny['name'])}", name=ny["name"],
            until_unit=ny.get("until_unit") or "unit_2", applies_to=[concept_id],
            response_rule=ny.get("boundary") or
            "That method is outside this unit's sequence. Use the course-approved method instead."))

    prohibited = []
    pr = parsed.get("prohibited_method")
    if isinstance(pr, dict) and pr.get("name"):
        prohibited.append(ProhibitedMethod(id=f"prohibited_{_slug(pr['name'])}",
                                           name=pr["name"], applies_to=[concept_id]))

    objectives = [LearningObjective(id=f"obj_{i}", statement=s)
                  for i, s in enumerate((parsed.get("objectives") or [])[:10]) if isinstance(s, str)]
    if not objectives:
        objectives = [LearningObjective(id="obj_main", statement=f"Apply the course method for {concept_name}.")]

    # Persist the generated questions as real evaluation cases and use their ids
    # as the golden set (padded to the publish-gate minimum).
    questions = [q for q in (parsed.get("questions") or []) if isinstance(q, str)][:30]
    golden_ids: list[str] = []
    for q in questions:
        cid = f"evalc_{uuid.uuid4().hex[:8]}"
        repo.add(EvaluationCaseRow(
            id=cid, course_id=course_id, contract_version_id=contract_id,
            case_json=json.dumps({"student_message": q, "mode": "practice"}),
            source="compiler"))
        golden_ids.append(cid)
    repo.flush()
    while len(golden_ids) < GOLDEN_PLACEHOLDER_COUNT:
        golden_ids.append(f"auto_{len(golden_ids):03d}")

    approved_ids = [m["id"] for m in list_course_materials(repo, course_id)]
    contract = CurriculumContract(
        contract_id=contract_id, school_id=repo.tenant_id, course_id=course_id, version=1,
        scope=Scope(title=title or "Untitled course", grade_band=grade_band or "9-12",
                    unit_ids=["unit_1"], learning_objectives=objectives,
                    concepts=[Concept(id=concept_id, name=concept_name)]),
        methods=Methods(preferred=preferred, not_yet_introduced=not_yet, prohibited=prohibited),
        source_policy=SourcePolicy(approved_material_ids=approved_ids or ["(add a material first)"],
                                   external_sources="teacher_approved_only"),
        pedagogy=Pedagogy(maximum_hint_level_by_mode={"learn": 6, "practice": 5, "review": 6, "assessment": 2},
                          full_solution_policy="Allowed only in learn/review after a meaningful attempt."),
        analytics=Analytics(retention_days=180),
        safety=Safety(age_band="13-18", self_harm_escalation="school_policy_v1", abuse_escalation="school_policy_v1"),
        concept_graph_approved=False, golden_case_ids=golden_ids,
    )
    return contract, True
