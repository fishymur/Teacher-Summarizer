"""Anthropic provider adapter (real).

Calls the Claude Messages API at https://api.anthropic.com/v1/messages using the
``x-api-key`` + ``anthropic-version: 2023-06-01`` headers. It asks the model for
a strict JSON object so the orchestrator and verifier receive structured
citations rather than having to parse prose.

This adapter is not exercised by the test suite (which uses the deterministic
stub); it runs when ANTHROPIC_API_KEY is set. Its purpose in this milestone is
to prove the interface is real and that a second provider slots in behind the
same Protocol without touching any curriculum rule.

Model ids and pricing change; set them via the constructor / env and verify
against current docs (https://platform.claude.com/docs/en/api/overview).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from .base import (
    GenerateRequest,
    GenerateResult,
    LLMProvider,
    ProviderCapabilities,
    ProviderDataPolicy,
    ResultCitation,
)

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

_SYSTEM = (
    "You are a curriculum-aligned tutor. You must obey the constraints supplied "
    "in each request: use only the approved source chunks provided, cite the "
    "exact anchor you used, keep help at or below the given hint level, use the "
    "required method, and never use a forbidden method. If a boundary message is "
    "provided, decline the restricted method using that wording and do not teach "
    "it. Respond ONLY with a JSON object, no prose or markdown fences, of the "
    "form: {\"response_text\": str, \"citations\": [{\"source_id\": str, "
    "\"anchor\": str, \"quote\": str}], \"hint_level\": int, \"methods_used\": "
    "[str], \"discloses_boundary\": bool}. Every quote must be copied verbatim "
    "from an approved chunk."
)


class AnthropicProvider:
    def __init__(
        self,
        model_id: str | None = None,
        *,
        api_key: str | None = None,
        max_tokens: int = 1024,
        region: str = "us",
        price_per_mtok_in: float = 0.0,
        price_per_mtok_out: float = 0.0,
        timeout: float = 45.0,
    ) -> None:
        self._model_id = model_id or os.environ.get("CCL_TUTOR_MODEL", "claude-sonnet-5")
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._max_tokens = max_tokens
        self._region = region
        self._price_in = price_per_mtok_in
        self._price_out = price_per_mtok_out
        self._timeout = timeout

    # --- prompt assembly ----------------------------------------------------

    def _user_content(self, req: GenerateRequest) -> str:
        chunks = [
            {"source_id": c.source_id, "anchor": c.anchor, "text": c.text}
            for c in req.allowed_chunks
        ]
        payload = {
            "mode": req.mode,
            "student_message": req.student_message,
            "student_attempt": req.student_attempt,
            "max_hint_level": req.target_hint_level,
            "required_method_ids": req.required_method_ids,
            "forbidden_method_ids": req.forbidden_method_ids,
            "method_names": req.method_names,
            "approved_chunks": chunks,
            "require_citations": req.require_citations,
            "full_solution_allowed": req.full_solution_allowed,
            "boundary_message": req.boundary_message,
        }
        scope_rule = {
            "disabled": "Answer ONLY from the approved course material above. Do not introduce outside facts.",
            "teacher_approved_only": "Ground your answer in the approved course material above. You may add limited, well-established general knowledge to clarify, but the material is the authority.",
            "enabled": "You may draw on general knowledge in addition to the approved material; still prefer and cite the material where it applies.",
        }.get(req.external_sources)
        text = json.dumps(payload, ensure_ascii=False)
        if scope_rule:
            text += f"\n\nSource policy: {scope_rule}"
        if req.revision_feedback:
            text += f"\n\nYour previous attempt failed these checks: {req.revision_feedback}. Fix them."
        return text

    def _messages(self, request: GenerateRequest) -> list[dict]:
        """Assemble the Messages array: recent history (mapped to user/assistant)
        followed by the current turn carrying the full structured payload."""
        msgs: list[dict] = []
        for h in request.history[-6:]:
            role = "assistant" if h.get("role") == "tutor" else "user"
            text = (h.get("text") or "").strip()
            if text:
                msgs.append({"role": role, "content": text})
        # The API requires the sequence to start with a user turn and alternate.
        while msgs and msgs[0]["role"] == "assistant":
            msgs.pop(0)

        text_content = self._user_content(request)
        if request.attempt_image:
            current = [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": request.attempt_image.get("media_type", "image/png"),
                    "data": request.attempt_image.get("data_b64", ""),
                }},
                {"type": "text", "text": text_content
                 + "\n\nThe image above is the student's handwritten attempt; read it and factor it into your hint."},
            ]
        else:
            current = text_content
        msgs.append({"role": "user", "content": current})
        return msgs

    async def generate(self, request: GenerateRequest) -> GenerateResult:
        if not self._api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        body = json.dumps(
            {
                "model": self._model_id,
                "max_tokens": self._max_tokens,
                "system": _SYSTEM,
                "messages": self._messages(request),
            }
        ).encode("utf-8")

        http_req = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            method="POST",
        )
        start = time.monotonic()
        with urllib.request.urlopen(http_req, timeout=self._timeout) as resp:
            request_id = resp.headers.get("request-id", "")
            data = json.loads(resp.read().decode("utf-8"))
        latency_ms = (time.monotonic() - start) * 1000.0

        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        parsed = self._parse_json(text)

        usage = data.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        cost = (in_tok / 1e6) * self._price_in + (out_tok / 1e6) * self._price_out

        return GenerateResult(
            response_text=parsed.get("response_text", ""),
            citations=[
                ResultCitation(c.get("source_id", ""), c.get("anchor", ""), c.get("quote", ""))
                for c in parsed.get("citations", [])
            ],
            hint_level=int(parsed.get("hint_level", 1)),
            methods_used=list(parsed.get("methods_used", [])),
            discloses_boundary=bool(parsed.get("discloses_boundary", False)),
            model_id=self._model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_ms=latency_ms,
            request_id=request_id,
        )

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fail safe: treat the whole thing as prose with no citations so the
            # verifier will reject it rather than the adapter crashing.
            return {"response_text": text, "citations": []}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("embeddings adapter is added with pgvector retrieval")

    def complete(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        """Plain-text completion for non-tutor calls (e.g. the compiler).

        Synchronous on purpose — callers use it outside the async tutor loop.
        Returns the concatenated text blocks, or "" on any failure so callers
        can fall back gracefully.
        """
        if not self._api_key:
            return ""
        body = json.dumps({
            "model": self._model_id, "max_tokens": max_tokens,
            "system": system, "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(
            API_URL, data=body,
            headers={"x-api-key": self._api_key, "anthropic-version": ANTHROPIC_VERSION,
                     "content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return ""
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def transcribe_images(self, images: list[dict], *, instructions: str) -> list[str]:
        """Transcribe text from images via the vision model. ``images`` is a list
        of ``{"media_type","data_b64"}``; returns one transcription per image
        (empty string for any that fail). Synchronous; used for material uploads
        (photos, scanned pages), not the tutor loop. Returns [] with no API key."""
        if not self._api_key:
            return []
        out: list[str] = []
        for img in images:
            content = [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": img.get("media_type", "image/png"),
                    "data": img.get("data_b64", "")}},
                {"type": "text", "text": instructions},
            ]
            body = json.dumps({
                "model": self._model_id, "max_tokens": 4000,
                "messages": [{"role": "user", "content": content}],
            }).encode("utf-8")
            req = urllib.request.Request(
                API_URL, data=body,
                headers={"x-api-key": self._api_key, "anthropic-version": ANTHROPIC_VERSION,
                         "content-type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                out.append("".join(b.get("text", "") for b in data.get("content", [])
                                   if b.get("type") == "text"))
            except Exception:  # noqa: BLE001
                out.append("")
        return out

    def data_policy(self) -> ProviderDataPolicy:
        return ProviderDataPolicy(
            provider="anthropic",
            region=self._region,
            training_disabled=True,  # API inputs are not used for training by default
            retention_days=30,
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            model_id=self._model_id,
            context_tokens=200_000,
            supports_tools=True,
            supports_structured_output=True,
        )


_a: LLMProvider = AnthropicProvider(api_key="")  # Protocol conformance check
