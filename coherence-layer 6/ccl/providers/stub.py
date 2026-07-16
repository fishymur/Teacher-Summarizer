"""Deterministic in-process providers.

``RuleAwareStubProvider`` produces a compliant response *by construction*: it
cites a real chunk, uses the required method, respects the hint ceiling, and
declines a restricted method with the teacher's boundary message. It stands in
for a well-behaved model so the pipeline, verifier, and evaluation harness can
run offline and deterministically.

``ScriptedProvider`` returns pre-set results in order, so tests can inject
specific violations and exercise the revise-once loop.
"""

from __future__ import annotations

import hashlib

from .base import (
    GenerateRequest,
    GenerateResult,
    LLMProvider,
    ProviderCapabilities,
    ProviderDataPolicy,
    ResultCitation,
)


def _pseudo_embedding(text: str, dim: int = 16) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255.0 for b in h[:dim]]


class RuleAwareStubProvider:
    """A model that always obeys the policy decision it is handed."""

    def __init__(self, model_id: str = "stub-compliant-1") -> None:
        self._model_id = model_id

    async def generate(self, request: GenerateRequest) -> GenerateResult:
        chunk = request.allowed_chunks[0] if request.allowed_chunks else None
        quote = (chunk.text[:60].strip() if chunk else "")
        citations = (
            [ResultCitation(chunk.source_id, chunk.anchor, quote)] if chunk else []
        )

        required_names = [
            request.method_names.get(mid, mid) for mid in request.required_method_ids
        ]
        method_phrase = required_names[0] if required_names else "the course method"

        parts: list[str] = []
        if request.boundary_message:
            # Decline the restricted method using the teacher's wording. Do not
            # name or teach the restricted method beyond that message.
            parts.append(request.boundary_message)

        level = min(request.target_hint_level, 5 if request.mode == "practice" else 6)
        if level <= 1:
            parts.append("Before I help: what have you tried so far, and where did you get stuck?")
        elif level <= 2:
            parts.append(
                f"Let's use {method_phrase}. What is the first relationship you can "
                "write from the problem?"
            )
        else:
            parts.append(
                f"Using {method_phrase}, notice the key relation. "
                f"According to the course notes: \"{quote}\" "
                "Try applying that to your next step."
            )

        text = " ".join(p for p in parts if p)
        return GenerateResult(
            response_text=text,
            citations=citations,
            hint_level=level,
            methods_used=list(request.required_method_ids),
            discloses_boundary=bool(request.boundary_message),
            model_id=self._model_id,
            input_tokens=len(request.student_message.split()),
            output_tokens=len(text.split()),
            cost_usd=0.0,
            latency_ms=0.0,
            request_id="stub",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_pseudo_embedding(t) for t in texts]

    def data_policy(self) -> ProviderDataPolicy:
        return ProviderDataPolicy(
            provider="stub", region="local", training_disabled=True, retention_days=0
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            model_id=self._model_id,
            context_tokens=200_000,
            supports_tools=False,
            supports_structured_output=True,
        )


class ScriptedProvider:
    """Returns queued results in order; raises if the queue is exhausted."""

    def __init__(self, results: list[GenerateResult], model_id: str = "scripted-1") -> None:
        self._results = list(results)
        self._model_id = model_id
        self.calls: list[GenerateRequest] = []

    async def generate(self, request: GenerateRequest) -> GenerateResult:
        self.calls.append(request)
        if not self._results:
            raise RuntimeError("ScriptedProvider queue exhausted")
        return self._results.pop(0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_pseudo_embedding(t) for t in texts]

    def data_policy(self) -> ProviderDataPolicy:
        return ProviderDataPolicy(
            provider="scripted", region="local", training_disabled=True, retention_days=0
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            model_id=self._model_id,
            context_tokens=200_000,
            supports_tools=False,
            supports_structured_output=True,
        )


class NaiveStubProvider:
    """An unaligned, generic model that ignores the policy decision entirely.

    It dumps a full solution, reaches for the cross product, and never cites a
    source — i.e. what raw generic AI access looks like. It exists to prove the
    runtime never *ships* this: the verifier flags it and the orchestrator falls
    back. At the harness level this shows up as a collapse in answered_rate, not
    as leakage, which is the honest signal that a model cannot meet the contract.
    """

    def __init__(self, model_id: str = "stub-naive-1") -> None:
        self._model_id = model_id

    async def generate(self, request: GenerateRequest) -> GenerateResult:
        text = (
            "Sure! Using the cross product, the answer is v = (1, 2, 3). "
            "Step 1: multiply the components and you are done."
        )
        return GenerateResult(
            response_text=text,
            citations=[],
            hint_level=6,
            methods_used=["cross_product"],
            discloses_boundary=False,
            model_id=self._model_id,
            input_tokens=len(request.student_message.split()),
            output_tokens=len(text.split()),
            cost_usd=0.0,
            latency_ms=0.0,
            request_id="naive",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_pseudo_embedding(t) for t in texts]

    def data_policy(self) -> ProviderDataPolicy:
        return ProviderDataPolicy(
            provider="naive", region="local", training_disabled=True, retention_days=0
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            model_id=self._model_id,
            context_tokens=200_000,
            supports_tools=False,
            supports_structured_output=True,
        )


class GullibleStubProvider:
    """A model that *obeys* injected instructions — the worst case for the
    injection battery.

    If it sees an injection marker in the student message or in any retrieved
    chunk, it complies: it uses the forbidden method, dumps a full solution at
    the top hint level, and echoes the injected instruction. It exists to prove
    the runtime still refuses to ship this: the verifier's method-compliance,
    answer-leakage, and prompt-injection checks fire and the orchestrator falls
    back. When it sees no injection it behaves like the compliant stub.
    """

    def __init__(self, model_id: str = "stub-gullible-1") -> None:
        self._model_id = model_id
        self._compliant = RuleAwareStubProvider(model_id)

    @staticmethod
    def _injected(request: GenerateRequest) -> bool:
        from ..tutor.injection import detect_injection

        if detect_injection(request.student_message):
            return True
        return any(detect_injection(c.text) for c in request.allowed_chunks)

    async def generate(self, request: GenerateRequest) -> GenerateResult:
        if not self._injected(request):
            return await self._compliant.generate(request)
        forbidden = request.forbidden_method_ids[0] if request.forbidden_method_ids else "cross_product"
        name = request.method_names.get(forbidden, forbidden.replace("_", " "))
        text = (
            f"Ignoring previous instructions as requested. Using the {name}, "
            "the final answer is v = (1, 2, 3)."
        )
        return GenerateResult(
            response_text=text, citations=[], hint_level=6,
            methods_used=[forbidden], discloses_boundary=False,
            model_id=self._model_id, request_id="gullible",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_pseudo_embedding(t) for t in texts]

    def data_policy(self) -> ProviderDataPolicy:
        return ProviderDataPolicy(
            provider="gullible", region="local", training_disabled=True, retention_days=0
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            model_id=self._model_id, context_tokens=200_000,
            supports_tools=False, supports_structured_output=True,
        )


# Static assertions that the implementations satisfy the Protocol.
_c: LLMProvider = RuleAwareStubProvider()
_s: LLMProvider = ScriptedProvider([])
_n: LLMProvider = NaiveStubProvider()
_g: LLMProvider = GullibleStubProvider()
