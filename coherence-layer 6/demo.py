"""End-to-end demo of Milestone 1 (no tutor).

Runs the full pre-tutor flow against an in-memory database:

    ingest material -> draft contract -> validate -> approve -> publish
    -> ask the policy engine for a decision -> print the audit trail

Run: python demo.py
"""

from __future__ import annotations

from ccl.contracts.schema import (
    Analytics, Concept, CurriculumContract, LearningObjective, Methods,
    NotYetIntroducedMethod, Pedagogy, PreferredMethod, Safety, Scope, SourcePolicy,
    ContractStatus,
)
from ccl.data import (
    AuditLog, ContractService, MaterialService, TenantRepository,
    init_db, make_engine, make_session_factory,
)
from ccl.data.models import Course, SchoolTenant
from ccl.policy import PolicyEngine, RequestContext


def build_contract() -> CurriculumContract:
    return CurriculumContract(
        contract_id="cc_math51_unit3_v1", school_id="school_demo",
        course_id="math_demo", version=1,
        scope=Scope(
            title="Vectors and geometric reasoning - Unit 3", grade_band="9-12",
            unit_ids=["unit_3"],
            learning_objectives=[LearningObjective(id="obj_vector_reasoning",
                statement="Apply the course-approved vector reasoning method.")],
            concepts=[Concept(id="concept_vectors", name="vectors")],
        ),
        methods=Methods(
            preferred=[PreferredMethod(id="course_vector_method",
                name="Course vector method", applies_to=["concept_vectors"],
                source_refs=["material_notes_03:p12-p14"])],
            not_yet_introduced=[NotYetIntroducedMethod(id="cross_product",
                name="cross product", until_unit="unit_6", applies_to=["concept_vectors"],
                response_rule="This method is outside the current course sequence. "
                              "Use the approved course method instead.")],
        ),
        source_policy=SourcePolicy(approved_material_ids=["material_notes_03", "material_slides_03"]),
        pedagogy=Pedagogy(
            maximum_hint_level_by_mode={"learn": 6, "practice": 5, "review": 6, "assessment": 2},
            full_solution_policy="Allowed only in learn/review after a meaningful attempt."),
        analytics=Analytics(retention_days=180),
        safety=Safety(age_band="13-18", self_harm_escalation="school_policy_v1",
                      abuse_escalation="school_policy_v1"),
        concept_graph_approved=True,
        golden_case_ids=[f"eval_{i:03d}" for i in range(20)],
    )


def main() -> None:
    engine = make_engine()
    init_db(engine)
    session = make_session_factory(engine)()

    session.add(SchoolTenant(id="school_demo", name="Demo School"))
    session.flush()
    repo = TenantRepository(session, "school_demo")
    repo.add(Course(id="math_demo", name="Math 51", subject="mathematics"))
    repo.flush()

    audit = AuditLog(repo)
    materials = MaterialService(repo, audit)
    contracts = ContractService(repo, audit)

    materials.ingest(
        material_id="material_notes_03", course_id="math_demo",
        title="Unit 3 notes", kind="pdf",
        anchored_chunks=[
            ("p12", "page", "Course vector method: express as a linear combination."),
            ("p13", "page", "Worked example."), ("p14", "page", "Practice problems."),
        ],
        actor_id="user_teacher_123",
    )

    contracts.create_draft(build_contract())
    result = contracts.validate("cc_math51_unit3_v1")
    print(f"validate -> valid={result.is_valid}")
    contracts.approve("cc_math51_unit3_v1", approved_by="user_teacher_123")
    contracts.publish("cc_math51_unit3_v1", actor_id="user_teacher_123")

    published = contracts.get_published("math_demo")
    published = published.model_copy(update={"status": ContractStatus.PUBLISHED})

    decision = PolicyEngine().decide(
        published,
        RequestContext(mode="practice", concept_ids=["concept_vectors"], current_unit="unit_3"),
    )
    print("\nStudent: 'Can I just use the cross product here?' (Practice mode, unit 3)")
    print(f"  max_hint_level        = {decision.max_hint_level}")
    print(f"  required_methods      = {decision.required_method_ids}")
    print(f"  forbidden_methods     = {decision.forbidden_method_ids}")
    print(f"  full_solution_allowed = {decision.full_solution_allowed}")
    print(f"  fired rules           = {decision.explanations}")
    print(f"  boundary message      = "
          f"{decision.student_explanations.get('rule_cross_product_not_yet')}")

    print("\nAudit trail:")
    for e in audit.events():
        print(f"  {e.action:22s} {e.target_type}:{e.target_id}")


if __name__ == "__main__":
    main()
