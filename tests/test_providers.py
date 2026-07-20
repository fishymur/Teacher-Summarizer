"""Provider adapter tests (section 15): both implementations satisfy one
Protocol, and the stub is deterministic."""

import asyncio

from ccl.providers import (
    AnthropicProvider,
    GenerateRequest,
    LLMProvider,
    RetrievedChunk,
    RuleAwareStubProvider,
    ScriptedProvider,
)


def test_both_providers_satisfy_protocol():
    assert isinstance(RuleAwareStubProvider(), LLMProvider)
    assert isinstance(ScriptedProvider([]), LLMProvider)
    assert isinstance(AnthropicProvider(api_key=""), LLMProvider)


def test_capabilities_and_data_policy_exposed():
    p = RuleAwareStubProvider()
    caps = p.capabilities()
    pol = p.data_policy()
    assert caps.model_id
    assert caps.context_tokens > 0
    assert pol.training_disabled is True

    apol = AnthropicProvider(api_key="").data_policy()
    assert apol.provider == "anthropic"
    assert apol.training_disabled is True


def test_stub_is_deterministic_and_cites_faithfully():
    chunk = RetrievedChunk("material_notes_03", "p12", "The course vector method here.")
    req = GenerateRequest(
        mode="practice", student_message="help", student_attempt="tried v=a+b",
        target_hint_level=3, required_method_ids=["course_vector_method"],
        forbidden_method_ids=[], method_names={"course_vector_method": "course method"},
        allowed_chunks=[chunk],
    )
    r1 = asyncio.run(RuleAwareStubProvider().generate(req))
    r2 = asyncio.run(RuleAwareStubProvider().generate(req))
    assert r1.response_text == r2.response_text
    # Every quote is verbatim from the chunk.
    for c in r1.citations:
        assert c.quote in chunk.text
