"""Shared fixtures for the milestone-1 acceptance tests.

The valid contract fixture is the Math 51 example from the build context: a
vectors unit where the course vector method is preferred and the cross product
is *not yet introduced* until unit 6.
"""

from __future__ import annotations

import pytest

from ccl.contracts.schema import (
    Analytics,
    Concept,
    CurriculumContract,
    LearningObjective,
    Methods,
    NotYetIntroducedMethod,
    Pedagogy,
    PreferredMethod,
    Safety,
    Scope,
    SourcePolicy,
)
from ccl.data import (
    AuditLog,
    ContractService,
    MaterialService,
    TenantRepository,
    init_db,
    make_engine,
    make_session_factory,
)
from ccl.data.models import Course, SchoolTenant


@pytest.fixture()
def session():
    engine = make_engine()
    init_db(engine)
    Session = make_session_factory(engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def repo(session):
    # Seed one tenant + course.
    tenant = SchoolTenant(id="school_demo", name="Demo School")
    session.add(tenant)
    session.flush()
    r = TenantRepository(session, tenant_id="school_demo")
    r.add(Course(id="math_demo", name="Math 51", subject="mathematics"))
    r.flush()
    return r


@pytest.fixture()
def audit(repo):
    return AuditLog(repo)


@pytest.fixture()
def seeded_material(repo, audit):
    """Ingest material_notes_03 with a resolvable page-12 anchor."""
    svc = MaterialService(repo, audit)
    svc.ingest(
        material_id="material_notes_03",
        course_id="math_demo",
        title="Unit 3 notes",
        kind="pdf",
        anchored_chunks=[
            ("p12", "page", "The course vector method: express as a linear combination."),
            ("p13", "page", "Worked example using the course method."),
            ("p14", "page", "Practice problems."),
        ],
        actor_id="user_teacher_123",
    )
    return "material_notes_03"


def make_valid_contract(**overrides) -> CurriculumContract:
    base = CurriculumContract(
        contract_id="cc_math51_unit3_v1",
        school_id="school_demo",
        course_id="math_demo",
        version=1,
        scope=Scope(
            title="Vectors and geometric reasoning - Unit 3",
            grade_band="9-12",
            unit_ids=["unit_3"],
            learning_objectives=[
                LearningObjective(
                    id="obj_vector_reasoning",
                    statement="Explain and apply the course-approved vector reasoning method.",
                )
            ],
            concepts=[Concept(id="concept_vectors", name="vectors")],
            prerequisite_assumptions=["concept_coordinates", "concept_linear_combinations"],
        ),
        methods=Methods(
            preferred=[
                PreferredMethod(
                    id="course_vector_method",
                    name="Course-approved vector/geometry method",
                    applies_to=["concept_vectors"],
                    source_refs=["material_notes_03:p12-p14"],
                )
            ],
            not_yet_introduced=[
                NotYetIntroducedMethod(
                    id="cross_product",
                    name="cross product",
                    until_unit="unit_6",
                    applies_to=["concept_vectors"],
                    response_rule=(
                        "This method is outside the current course sequence. "
                        "Use the approved course method instead."
                    ),
                )
            ],
        ),
        source_policy=SourcePolicy(
            approved_material_ids=["material_notes_03", "material_slides_03"],
            external_sources="teacher_approved_only",
        ),
        pedagogy=Pedagogy(
            maximum_hint_level_by_mode={"learn": 6, "practice": 5, "review": 6, "assessment": 2},
            full_solution_policy="Allowed only in learn/review after a meaningful attempt.",
        ),
        analytics=Analytics(retention_days=180),
        safety=Safety(
            age_band="13-18",
            self_harm_escalation="school_policy_v1",
            abuse_escalation="school_policy_v1",
        ),
        concept_graph_approved=True,
        golden_case_ids=[f"eval_{i:03d}" for i in range(20)],
    )
    if overrides:
        base = base.model_copy(update=overrides)
    return base


@pytest.fixture()
def valid_contract():
    return make_valid_contract()


@pytest.fixture()
def contract_service(repo, audit):
    return ContractService(repo, audit)


@pytest.fixture()
def published_contract(contract_service, seeded_material):
    """Publish the Math 51 contract through the real lifecycle and return it."""
    from ccl.contracts.schema import ContractStatus

    contract_service.create_draft(make_valid_contract())
    result = contract_service.validate("cc_math51_unit3_v1")
    assert result.is_valid, sorted(c.value for c in result.codes)
    contract_service.approve("cc_math51_unit3_v1", approved_by="user_teacher_123")
    contract_service.publish("cc_math51_unit3_v1", actor_id="user_teacher_123")
    c = contract_service.get_published("math_demo")
    return c.model_copy(update={"status": ContractStatus.PUBLISHED})


@pytest.fixture()
def orchestrator(repo, audit):
    from ccl.providers import RuleAwareStubProvider
    from ccl.tutor import TutorOrchestrator

    return TutorOrchestrator(repo, audit, RuleAwareStubProvider())
