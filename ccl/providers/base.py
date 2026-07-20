"""Model-provider adapter (section 15).

The adapter is the *only* place a language model is touched. Curriculum rules
never live inside a provider prompt: the orchestrator passes the policy decision
and retrieved evidence in as structured data, and the verifier checks the output
against that same structured data. Swapping providers therefore cannot change
what the contract permits.

The Protocol is async, as specified. Two implementations ship in this milestone:
a deterministic in-process stub (for hermetic tests and the headless harness)
and a real Anthropic adapter (exercised when an API key is present).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RetrievedChunk:
    source_id: str  # material id, e.g. "material_notes_03"
    anchor: str     # anchor label, e.g. "p12"
    text: str


@dataclass
class GenerateRequest:
    mode: str
    student_message: str
    student_attempt: str
    target_hint_level: int
    required_method_ids: list[str]
    forbidden_method_ids: list[str]
    # method_id -> human name, for both the model prompt and the verifier scan.
    method_names: dict[str, str] = field(default_factory=dict)
    # method_id -> surface terms/aliases the verifier scans for (leakage check).
    forbidden_method_terms: dict[str, list[str]] = field(default_factory=dict)
    allowed_chunks: list[RetrievedChunk] = field(default_factory=list)
    require_citations: bool = True
    # Source scope: "disabled" (course material only), "teacher_approved_only"
    # (may lightly supplement), or "enabled" (may use outside knowledge freely).
    external_sources: str = "teacher_approved_only"
    full_solution_allowed: bool = False
    # Present only when a restricted method was requested. Pre-authored by the
    # teacher; the tutor must not teach the restricted method itself.
    boundary_message: str | None = None
    # Structured feedback from a failed verification, used on the single retry.
    revision_feedback: str | None = None
    # Optional photo of the student's handwritten attempt. {"media_type","data_b64"}.
    # Vision-capable providers read it; others ignore it (text attempt still applies).
    attempt_image: dict | None = None
    # Recent conversation, oldest first: [{"role":"student"|"tutor","text":str}, ...].
    # Provides context for generation only; the policy decision and verifier are
    # always recomputed for the current turn, so history cannot loosen the rules.
    history: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ResultCitation:
    source_id: str
    anchor: str
    quote: str


@dataclass
class GenerateResult:
    response_text: str
    citations: list[ResultCitation] = field(default_factory=list)
    # Model self-reports (advisory only; compliance is checked against text).
    hint_level: int = 1
    methods_used: list[str] = field(default_factory=list)
    discloses_boundary: bool = False
    # Telemetry.
    model_id: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    request_id: str = ""


@dataclass(frozen=True)
class ProviderDataPolicy:
    provider: str
    region: str
    training_disabled: bool
    retention_days: int


@dataclass(frozen=True)
class ProviderCapabilities:
    model_id: str
    context_tokens: int
    supports_tools: bool
    supports_structured_output: bool


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(self, request: GenerateRequest) -> GenerateResult: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    def data_policy(self) -> ProviderDataPolicy: ...

    def capabilities(self) -> ProviderCapabilities: ...
