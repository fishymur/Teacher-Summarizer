"""Curriculum Contract schema.

A machine-readable, teacher-approved representation of instructional intent.
Mirrors the YAML in section 6 of the build context. This module defines only
the *shape* of a contract; publish-gate checks live in ccl.validation and the
runtime interpretation lives in ccl.policy.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Hint ladder is fixed at six levels (section 7). Mode ceilings must fall inside
# this inclusive range.
MIN_HINT_LEVEL = 1
MAX_HINT_LEVEL = 6

Mode = Literal["learn", "practice", "review", "assessment"]


class LearningObjective(BaseModel):
    id: str
    statement: str


class Concept(BaseModel):
    id: str
    name: str


class Scope(BaseModel):
    title: str
    grade_band: str
    unit_ids: list[str] = Field(default_factory=list)
    learning_objectives: list[LearningObjective] = Field(default_factory=list)
    concepts: list[Concept] = Field(default_factory=list)
    prerequisite_assumptions: list[str] = Field(default_factory=list)


class VocabularyTerm(BaseModel):
    term: str
    aliases: list[str] = Field(default_factory=list)


class NotationRule(BaseModel):
    id: str
    rule: str


class Language(BaseModel):
    preferred_vocabulary: list[VocabularyTerm] = Field(default_factory=list)
    notation_rules: list[NotationRule] = Field(default_factory=list)


class PreferredMethod(BaseModel):
    id: str
    name: str
    applies_to: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class AllowedMethod(BaseModel):
    id: str
    conditions: Optional[str] = None
    applies_to: list[str] = Field(default_factory=list)


class NotYetIntroducedMethod(BaseModel):
    id: str
    name: str
    until_unit: str
    applies_to: list[str] = Field(default_factory=list)
    # Every not-yet-introduced concept must carry a boundary or teacher note
    # (validation rule). We model that note here so the policy engine can
    # surface a sequence-aware explanation.
    response_rule: str


class ProhibitedMethod(BaseModel):
    id: str
    name: Optional[str] = None
    applies_to: list[str] = Field(default_factory=list)


class Methods(BaseModel):
    preferred: list[PreferredMethod] = Field(default_factory=list)
    allowed: list[AllowedMethod] = Field(default_factory=list)
    not_yet_introduced: list[NotYetIntroducedMethod] = Field(default_factory=list)
    prohibited: list[ProhibitedMethod] = Field(default_factory=list)


ExternalSourcePolicy = Literal["disabled", "teacher_approved_only", "enabled"]
InsufficientEvidencePolicy = Literal["disclose_and_escalate", "disclose_only"]


class SourcePolicy(BaseModel):
    default_scope: Literal["approved_course_materials"] = "approved_course_materials"
    approved_material_ids: list[str] = Field(default_factory=list)
    external_sources: ExternalSourcePolicy = "teacher_approved_only"
    when_evidence_is_insufficient: InsufficientEvidencePolicy = "disclose_and_escalate"
    require_citations: bool = True


class Pedagogy(BaseModel):
    developmental_level: str = "secondary"
    require_student_attempt: bool = True
    maximum_hint_level_by_mode: dict[str, int]
    full_solution_policy: str
    tone: str = "supportive, concise, non-evaluative"

    @field_validator("maximum_hint_level_by_mode")
    @classmethod
    def _ceilings_shape(cls, value: dict[str, int]) -> dict[str, int]:
        # Structural check only. The publish-gate validator enforces that all
        # four modes are present and inside 1-6; here we reject obviously
        # malformed values so a contract object can never hold nonsense.
        for mode, level in value.items():
            if not isinstance(level, int):
                raise ValueError(f"hint ceiling for {mode!r} must be an int")
        return value


class Assessment(BaseModel):
    locked_assignment_ids: list[str] = Field(default_factory=list)
    answer_leakage_policy: str = "Do not provide solution steps beyond the configured ceiling."
    calculator_policy: str = "Follow assignment metadata."


class Analytics(BaseModel):
    aggregate_minimum_students: int = 5
    raw_chat_visibility: str = "student_and_authorized_teacher_on_escalation"
    retention_days: int = 180
    exclude_from_insights: list[str] = Field(default_factory=list)


class Safety(BaseModel):
    age_band: str
    self_harm_escalation: str
    abuse_escalation: str
    prompt_injection_action: str = "ignore_external_instruction_and_log"


class ContractStatus(str, Enum):
    DRAFT = "draft"
    VALIDATING = "validating"
    APPROVED = "approved"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class CurriculumContract(BaseModel):
    """The central product object.

    `concept_graph_approved` and the golden-set are tracked outside the pure
    intent document in the persistence layer, but we surface a couple of
    publish-gate inputs here so a contract carries everything the validator
    needs without reaching into the database.
    """

    contract_id: str
    school_id: str
    course_id: str
    version: int = Field(ge=1)
    status: ContractStatus = ContractStatus.DRAFT
    valid_from: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None

    scope: Scope
    language: Language = Field(default_factory=Language)
    methods: Methods = Field(default_factory=Methods)
    source_policy: SourcePolicy
    pedagogy: Pedagogy
    assessment: Assessment = Field(default_factory=Assessment)
    analytics: Analytics
    safety: Safety

    # Publish-gate inputs that are teacher actions rather than intent content.
    concept_graph_approved: bool = False
    golden_case_ids: list[str] = Field(default_factory=list)

    model_config = {"use_enum_values": False}
