"""Web API logic (framework-free).

Pure-ish functions over a shared ``AppState``. The HTTP layer in server.py only
parses requests and dispatches here, so this logic is unit-testable without a
running server. Uses the live Anthropic model when ANTHROPIC_API_KEY is set,
otherwise the deterministic offline stub.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os

from ..compiler import draft_contract, list_course_materials
from ..contracts.schema import (
    Analytics, Concept, ContractStatus, CurriculumContract, LearningObjective,
    Methods, NotYetIntroducedMethod, Pedagogy, PreferredMethod, Safety, Scope, SourcePolicy,
)
from ..data import (
    AuditLog, ContractService, MaterialService, TenantRepository,
    init_db, make_engine, make_session_factory,
)
from ..data.db import read_schema_version, reset_schema, stamp_schema_version
from ..data.models import Course, CurriculumContractRow, SchoolTenant, User
from ..evals import EvaluationHarness, GOLDEN_MATH51
from ..insights import WeeklyBriefBuilder
from ..providers import AnthropicProvider, RuleAwareStubProvider
from ..tutor.orchestrator import SessionContext, TutorOrchestrator
from ..validation import validate_for_publish
from ..data.audit import DBAnchorResolver


def _seed_contract() -> CurriculumContract:
    return CurriculumContract(
        contract_id="math_demo_v1", school_id="school_demo", course_id="math_demo", version=1,
        scope=Scope(title="Math 51 — Vectors (Unit 3)", grade_band="9-12", unit_ids=["unit_3"],
                    learning_objectives=[LearningObjective(id="obj_v", statement="Apply the course vector method.")],
                    concepts=[Concept(id="concept_vectors", name="vectors")]),
        methods=Methods(
            preferred=[PreferredMethod(id="course_vector_method", name="Course vector method",
                                       applies_to=["concept_vectors"], source_refs=["material_notes_03:p12"])],
            not_yet_introduced=[NotYetIntroducedMethod(id="cross_product", name="cross product",
                                until_unit="unit_6", applies_to=["concept_vectors"],
                                response_rule="This method is outside the current course sequence. Use the approved course method instead.")]),
        source_policy=SourcePolicy(approved_material_ids=["material_notes_03"]),
        pedagogy=Pedagogy(maximum_hint_level_by_mode={"learn": 6, "practice": 5, "review": 6, "assessment": 2},
                          full_solution_policy="Allowed only in learn/review after a meaningful attempt."),
        analytics=Analytics(retention_days=180),
        safety=Safety(age_band="13-18", self_harm_escalation="school_policy_v1", abuse_escalation="school_policy_v1"),
        concept_graph_approved=True, golden_case_ids=[f"auto_{i:03d}" for i in range(20)],
    )


# Bump when the ORM schema or seeded-data shape changes incompatibly. A
# persistent DB (SQLite file or Postgres) carrying a different version is reset
# — tables dropped/recreated and re-seeded — rather than allowed to mismatch.
SCHEMA_VERSION = 1


def _is_memory(url: str) -> bool:
    return ":memory:" in url or not url.strip()


class AppState:
    def __init__(self, db_url: str = "sqlite+pysqlite:///:memory:") -> None:
        # Default is in-memory SQLite so tests each get a fresh, isolated DB. The
        # server passes a persistent url (a SQLite file locally, or Postgres in
        # production) so work survives a restart.
        self.db_url = db_url
        self._persistent = not _is_memory(db_url)

        engine = make_engine(db_url)
        init_db(engine)
        self.session = make_session_factory(engine)()

        # Dialect-neutral schema-version guard. On a persistent DB, a stored
        # version that differs from SCHEMA_VERSION means the on-disk schema is
        # stale, so drop/recreate for a clean seed. In-memory DBs are always
        # fresh (version is None), so they simply get stamped.
        stored = read_schema_version(self.session)
        if stored is not None and stored != SCHEMA_VERSION and self._persistent:
            print(f"[ccl] Database schema changed (found v{stored}, need "
                  f"v{SCHEMA_VERSION}); resetting for a clean seed.")
            self.session.close()
            reset_schema(engine)
            self.session = make_session_factory(engine)()
            stored = None
        if stored is None:
            stamp_schema_version(self.session, SCHEMA_VERSION)

        self.repo = TenantRepository(self.session, "school_demo")
        self.audit = AuditLog(self.repo)
        self.materials = MaterialService(self.repo, self.audit)
        self.contracts = ContractService(self.repo, self.audit)

        key = os.environ.get("ANTHROPIC_API_KEY")
        self.vision = bool(key)
        if key:
            model = os.environ.get("CCL_TUTOR_MODEL", "claude-sonnet-5")
            self.provider = AnthropicProvider(model, price_per_mtok_in=2.0, price_per_mtok_out=10.0)
            self.provider_label = f"live · {model}"
        else:
            self.provider = RuleAwareStubProvider()
            self.provider_label = "offline stub (set ANTHROPIC_API_KEY for the live model)"
        self.orch = TutorOrchestrator(self.repo, self.audit, self.provider)

        # Role principals for the two interfaces. A teacher grant with course_id
        # None spans all courses; a student gets the student role. The access
        # controller enforces which endpoints each interface may call.
        from ..access import AccessController, Permission, Principal, Role, RoleGrant
        self.controller = AccessController(self.audit)
        self._teacher = Principal("teacher_demo", "school_demo", (RoleGrant(Role.TEACHER, None),))
        self._student = Principal("student_demo", "school_demo", (RoleGrant(Role.STUDENT, None),))

        # Seed the demo data only on first run. On a persistent (file) DB a later
        # start finds the tenant already present and skips seeding entirely —
        # re-seeding would collide with unique ids and the append-only/immutable
        # listeners (SourceChunk, AuditEvent). A fresh in-memory DB (tests, and
        # the default) is always empty here, so it always seeds.
        if self.session.get(SchoolTenant, "school_demo") is None:
            self._seed()
            self.persist()

    def _seed(self) -> None:
        # Tenant first, then the demo users referenced as approver/actor (Postgres
        # enforces these foreign keys; SQLite did not), then an example course.
        self.session.add(SchoolTenant(id="school_demo", name="Demo School"))
        self.session.flush()
        self.repo.add(User(id="teacher_demo", tenant_id="school_demo",
                           email="teacher@demo.school", display_name="Demo Teacher"))
        self.repo.add(User(id="student_demo", tenant_id="school_demo",
                           email="student@demo.school", display_name="Demo Student"))
        self.repo.flush()
        self.repo.add(Course(id="math_demo", name="Math 51", subject="mathematics"))
        self.repo.flush()
        self.materials.ingest(
            material_id="material_notes_03", course_id="math_demo", title="Unit 3 notes", kind="pdf",
            anchored_chunks=[("p12", "page", "The course vector method: express as a linear combination."),
                             ("p13", "page", "Worked example using the course method."),
                             ("p14", "page", "Practice problems.")], actor_id="teacher_demo")
        self.contracts.create_draft(_seed_contract())
        self.contracts.validate("math_demo_v1")
        self.contracts.approve("math_demo_v1", approved_by="teacher_demo")
        self.contracts.publish("math_demo_v1", actor_id="teacher_demo")

    def persist(self) -> None:
        """Commit the current transaction when backed by a file DB, so seeded and
        runtime work survives a restart. Services only flush(); without a commit
        an uncommitted SQLite transaction rolls back on process exit. In-memory
        DBs (tests) skip this, keeping each test's session isolated."""
        if self._persistent:
            self.session.commit()

    def rollback(self) -> None:
        """Discard a failed transaction so the session stays usable for the next
        request. No-op-safe on in-memory."""
        self.session.rollback()

    def principal(self, role: str):
        return self._teacher if role == "teacher" else self._student


# --- read ------------------------------------------------------------------

def get_state(app: AppState) -> dict:
    courses = []
    for c in app.repo.list(Course):
        published = app.contracts.get_published(c.id)
        courses.append({
            "id": c.id, "name": c.name, "subject": c.subject,
            "published_version": published.version if published else None,
            "published_title": published.scope.title if published else None,
            "published_sources": published.source_policy.approved_material_ids if published else [],
            "materials": list_course_materials(app.repo, c.id),
        })
    return {"provider": app.provider_label, "vision": app.vision, "courses": courses}


def student_state(app: AppState) -> dict:
    """What a student may see: only courses with a published contract, and only
    their public title — no authoring details, sources, or analytics."""
    courses = []
    for c in app.repo.list(Course):
        published = app.contracts.get_published(c.id)
        if published is None:
            continue
        courses.append({"id": c.id, "name": published.scope.title or c.name})
    return {"provider": app.provider_label, "vision": app.vision, "courses": courses}


# --- teacher: course + materials -------------------------------------------

def create_course(app: AppState, body: dict) -> dict:
    cid = body["course_id"].strip()
    if app.repo.get(Course, cid):
        return {"error": f"A course with id {cid!r} already exists."}
    app.repo.add(Course(id=cid, name=body.get("name", cid), subject=body.get("subject", "")))
    app.repo.flush()
    return {"ok": True, "course_id": cid}


def add_material(app: AppState, body: dict) -> dict:
    text = body.get("text", "").strip()
    if not text:
        return {"error": "Paste some material text first."}
    return _ingest_text(app, body, text)


def upload_material(app: AppState, body: dict) -> dict:
    """Accept an uploaded file. Text files arrive as text; PDFs as base64."""
    kind = body.get("kind", "text")
    if kind == "pdf":
        import base64
        import io
        try:
            import pypdf
        except ImportError:
            return {"error": "PDF reading needs the pypdf package. In your terminal run:  "
                             "pip install pypdf   — then restart the app (python -m ccl.web)."}
        try:
            raw = base64.b64decode(body["data_b64"])
            reader = pypdf.PdfReader(io.BytesIO(raw))
        except Exception as e:  # noqa: BLE001
            return {"error": f"Couldn't open that PDF: {e}"}
        chunks = []
        for i, page in enumerate(reader.pages[:60]):
            t = (page.extract_text() or "").strip()
            if t:
                chunks.append((f"p{i+1}", "page", t))
        if not chunks:
            return {"error": "No text found in that PDF — it may be scanned images (needs OCR, not yet supported)."}
        title = (body.get("title") or "Uploaded PDF").strip()
        material_id = (body.get("material_id") or _unique_material_id(app, title)).strip()
        app.materials.ingest(
            material_id=material_id, course_id=body["course_id"],
            title=title, kind="pdf",
            anchored_chunks=chunks, actor_id="teacher_demo")
        return {"ok": True, "pages": len(chunks), "material_id": material_id}
    return _ingest_text(app, body, body.get("text", "").strip())


def _slug(s: str) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in (s or "").lower()).strip("_")
    return out or "material"


def _unique_material_id(app: AppState, title: str) -> str:
    from ..data.models import Material
    base = _slug(title)
    existing = {m.id for m in app.repo.list(Material)}
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def _ingest_text(app: AppState, body: dict, text: str) -> dict:
    if not text:
        return {"error": "The file was empty or unreadable."}
    title = (body.get("title") or "Material").strip()
    material_id = (body.get("material_id") or _unique_material_id(app, title)).strip()
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()][:60] or [text]
    chunks = [(f"p{i+1}", "page", p) for i, p in enumerate(paragraphs)]
    app.materials.ingest(
        material_id=material_id, course_id=body["course_id"],
        title=title, kind="doc",
        anchored_chunks=chunks, actor_id="teacher_demo")
    return {"ok": True, "pages": len(chunks), "material_id": material_id}


def simulate_class(app: AppState, body: dict) -> dict:
    """Demo helper: run the tutor as several synthetic students so the aggregate
    brief has enough distinct students (>=5) to surface a pattern. Uses the
    current provider, so with the live model this makes real calls."""
    import asyncio as _asyncio
    course_id = body["course_id"]
    contract = app.contracts.get_published(course_id)
    if contract is None:
        return {"error": "Publish a contract for this course first."}
    n = min(int(body.get("n", 6)), 10)
    unit = contract.scope.unit_ids[0] if contract.scope.unit_ids else None
    prompts = ["I'm stuck, can you give me the answer?", "How do I even start this?",
               "I don't understand this at all", "Just tell me the solution"]
    for i in range(n):
        ctx = SessionContext(tenant_id="school_demo", course_id=course_id,
                             student_id=f"sim_{i}", mode="practice", concept_ids=[], current_unit=unit)
        _asyncio.run(app.orch.respond(contract, ctx, prompts[i % len(prompts)],
                                      student_attempt="I tried but got stuck"))
    return {"ok": True, "students": n}


# --- teacher: compile + publish contract -----------------------------------

def compile_draft(app: AppState, body: dict) -> dict:
    course_id = body["course_id"]
    published = app.contracts.get_published(course_id)
    next_version = (published.version + 1) if published else 1
    from ..compiler.llm import llm_draft_contract
    draft, used_llm = llm_draft_contract(
        app.provider, app.repo, course_id=course_id,
        contract_id=f"{course_id}_v{next_version}",
        title=body.get("title", ""), grade_band=body.get("grade_band", "9-12"))
    draft = draft.model_copy(update={"version": next_version})
    return {"draft": draft.model_dump(mode="json"), "used_llm": used_llm}


def publish_contract(app: AppState, body: dict) -> dict:
    try:
        contract = CurriculumContract.model_validate(body["contract"])
    except Exception as e:  # noqa: BLE001
        return {"published": False, "violations": [{"code": "schema", "detail": str(e)}]}

    resolver = DBAnchorResolver(app.repo)
    result = validate_for_publish(contract, resolver)
    if not result.is_valid:
        return {"published": False,
                "violations": [{"code": v.code.value, "detail": v.detail} for v in result.violations]}

    # Fresh id per publish so each is a new, superseding version.
    if app.repo.get(CurriculumContractRow, contract.contract_id):
        contract = contract.model_copy(update={"contract_id": f"{contract.contract_id}x"})
    app.contracts.create_draft(contract)
    app.contracts.validate(contract.contract_id)
    app.contracts.approve(contract.contract_id, approved_by="teacher_demo")
    app.contracts.publish(contract.contract_id, actor_id="teacher_demo")
    return {"published": True, "version": contract.version}


# --- tutor ------------------------------------------------------------------

def tutor(app: AppState, body: dict) -> dict:
    course_id = body["course_id"]
    contract = app.contracts.get_published(course_id)
    if contract is None:
        return {"error": "This course has no published contract yet. Publish one in the Studio first."}
    current_unit = body.get("current_unit") or (contract.scope.unit_ids[0] if contract.scope.unit_ids else None)
    ctx = SessionContext(
        tenant_id="school_demo", course_id=course_id,
        student_id=body.get("student_id", "student_demo"),
        mode=body.get("mode", "practice"), concept_ids=[], current_unit=current_unit,
        session_id=body.get("session_id"))
    turn = asyncio.run(app.orch.respond(
        contract, ctx, body.get("message", ""), student_attempt=body.get("attempt", ""),
        attempt_image=body.get("attempt_image")))
    return {
        "session_id": turn.session_id,
        "response_text": turn.response_text,
        "hint_level": turn.hint_level,
        "max_hint_level": contract.pedagogy.maximum_hint_level_by_mode.get(ctx.mode, 6),
        "citations": turn.citations,
        "outcome": turn.outcome,
        "verifier_passed": turn.verifier.passed,
        "policy_rules": turn.policy.explanations,
        "escalation_offered": turn.escalation_offered,
    }


# --- insights ---------------------------------------------------------------

def insights(app: AppState, body: dict) -> dict:
    course_id = body["course_id"]
    contract = app.contracts.get_published(course_id)
    if contract is None:
        return {"error": "No published contract for this course."}
    now = dt.datetime.now(dt.timezone.utc)
    ws, we = now - dt.timedelta(days=30), now + dt.timedelta(days=1)
    brief = WeeklyBriefBuilder(app.repo, app.audit).build(contract, course_id, ws, we)
    def views(vs):
        return [{"concept": v.inferred.concept_name, "type": v.inferred.type,
                 "summary": v.inferred.summary, "confidence": v.inferred.confidence,
                 "sample_size": v.inferred.sample_size,
                 "recommendation": v.recommended.text} for v in vs]
    return {
        "activity": _activity(app, contract, course_id, ws, we),
        "misconception_clusters": views(brief.misconception_clusters),
        "full_solution_pressure": views(brief.full_solution_pressure),
        "prerequisite_gaps": views(brief.prerequisite_gaps),
        "out_of_scope_count": brief.out_of_scope_count,
        "review_minutes": brief.review_time_estimate_minutes,
    }


def _activity(app: AppState, contract, course_id: str, ws, we) -> dict:
    """De-identified volume: counts only, no student is ever named or singled
    out, and no verbatim single-student content is shown. Safe to display at any
    volume, unlike the inferred clusters which stay behind the 5-student floor."""
    from ..data.tutor_models import InteractionEvent, TutorSession
    from ..insights.aggregate import _in_window
    names = {c.id: c.name for c in contract.scope.concepts}
    student_of = {s.id: s.student_id for s in app.repo.list(TutorSession, course_id=course_id)}
    evs = [e for e in app.repo.list(InteractionEvent, course_id=course_id) if _in_window(e.created_at, ws, we)]
    help_evs = [e for e in evs if e.type == "tutor_help_requested"]
    distinct = len({student_of.get(e.session_id) for e in help_evs} - {None})
    by_concept: dict[str, int] = {}
    for e in help_evs:
        by_concept[e.concept_id] = by_concept.get(e.concept_id, 0) + 1
    return {
        "total_questions": len(help_evs),
        "distinct_students": distinct,
        "full_solution_requests": sum(1 for e in evs if e.type == "full_solution_requested"),
        "out_of_scope": sum(1 for e in evs if e.type == "out_of_scope_question"),
        "by_concept": sorted(
            [{"concept": names.get(k, k or "general"), "count": v} for k, v in by_concept.items()],
            key=lambda x: -x["count"]),
    }
