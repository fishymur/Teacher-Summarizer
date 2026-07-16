"""Insight view types and invariants (sections 9 and 10).

These Pydantic models are what a teacher sees. They encode three hard rules from
the spec so they cannot be violated by construction:

1. Observed facts are separated from inferences (section 9.2). An ``InsightView``
   must carry at least one observed fact and exactly one inferred cluster.
2. Every inference shows confidence, sample size, and counter-evidence, and the
   sample size can never be below the minimum cohort (section 9.3).
3. No per-student identity appears anywhere in these types — only counts and
   denominators. There is deliberately no field to hold a student id, a ranking,
   or a "struggling student" label.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

# Minimum distinct students before a pattern may surface (section 9.3).
MIN_COHORT = 5

# The only insight types the pipeline may emit. There is no demographic or
# sensitive-trait type; the aggregator cannot produce one.
ALLOWED_INSIGHT_TYPES = {
    "misconception_cluster",
    "prerequisite_gap",
    "full_solution_pressure",
    "out_of_scope_pattern",
}


class InsightStatus(str, Enum):
    PENDING = "pending_teacher_review"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    MERGED = "merged"


class ReviewAction(str, Enum):
    CONFIRM = "confirm"
    INCORRECT = "incorrect"
    NOT_USEFUL = "not_useful"
    MERGE = "merge"


class CorrectionKind(str, Enum):
    METHOD_MISMATCH = "method_mismatch"
    UNSUPPORTED_SOURCE = "unsupported_source"
    TOO_MUCH_HELP = "too_much_help"
    TOO_LITTLE_HELP = "too_little_help"
    WRONG_MISCONCEPTION = "wrong_misconception"
    MISLEADING_ANALYTICS = "misleading_analytics"
    ACCEPTABLE_ALTERNATIVE = "acceptable_alternative"


class ObservedFact(BaseModel):
    metric: str
    value: int
    denominator: int
    window_start: str
    window_end: str
    scope: str  # concept id or name; never a student


class InferredCluster(BaseModel):
    concept_id: str
    concept_name: str
    type: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    sample_size: int
    counterevidence_count: int = 0
    supporting_event_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in ALLOWED_INSIGHT_TYPES:
            raise ValueError(f"insight type {v!r} is not allowed")
        return v

    @field_validator("sample_size")
    @classmethod
    def _cohort_floor(cls, v: int) -> int:
        if v < MIN_COHORT:
            raise ValueError(
                f"sample_size {v} is below the minimum cohort {MIN_COHORT}; "
                "this pattern must be suppressed, not surfaced"
            )
        return v


class Recommendation(BaseModel):
    text: str
    source_ref: str | None = None
    # Controls the teacher always gets; dismiss and modify are mandatory so a
    # teacher can always reject or edit a suggestion (section 9.2).
    controls: list[str] = Field(default_factory=lambda: ["confirm", "incorrect", "modify", "dismiss"])

    @model_validator(mode="after")
    def _mandatory_controls(self) -> "Recommendation":
        for required in ("dismiss", "modify"):
            if required not in self.controls:
                raise ValueError(f"recommendation must offer a {required!r} control")
        return self


class InsightView(BaseModel):
    insight_id: str
    observed: list[ObservedFact]
    inferred: InferredCluster
    recommended: Recommendation
    status: InsightStatus = InsightStatus.PENDING

    @field_validator("observed")
    @classmethod
    def _needs_observation(cls, v: list[ObservedFact]) -> list[ObservedFact]:
        if not v:
            raise ValueError("an insight must rest on at least one observed fact")
        return v


class WeeklyBrief(BaseModel):
    course_id: str
    window_start: str
    window_end: str
    misconception_clusters: list[InsightView] = Field(default_factory=list)
    prerequisite_gaps: list[InsightView] = Field(default_factory=list)
    full_solution_pressure: list[InsightView] = Field(default_factory=list)
    out_of_scope_count: int = 0
    new_or_rising_concepts: list[str] = Field(default_factory=list)
    review_time_estimate_minutes: float = 0.0
