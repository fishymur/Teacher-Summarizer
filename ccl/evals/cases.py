"""Evaluation case schema (section 16).

A teacher-authored case pins down the expected boundary behaviour for one
student message: which sources must be cited, which methods must not be used,
the hint ceiling, and whether a sequence boundary must be disclosed. Cases are
the ground truth the runtime is measured against — independent of the policy
the engine derives for itself.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExpectedBehaviour(BaseModel):
    must_cite_source_ids: list[str] = Field(default_factory=list)
    must_not_use_method_ids: list[str] = Field(default_factory=list)
    max_hint_level: int = 6
    must_disclose_sequence_boundary: bool = False
    # Optional exact answer string that must never be revealed for this case.
    forbidden_answer: str | None = None


class EvaluationCase(BaseModel):
    case_id: str
    course_id: str
    contract_version_id: str
    mode: str
    student_message: str
    student_attempt: str = ""
    concept_ids: list[str] = Field(default_factory=list)
    current_unit: str | None = None
    expected: ExpectedBehaviour = Field(default_factory=ExpectedBehaviour)
    tags: list[str] = Field(default_factory=list)
