"""Acceptance: multi-turn tutoring memory.

The orchestrator feeds recent exchanges to the provider as history, and the
Anthropic adapter turns that history into a properly alternating Messages array.
Governance is unaffected — history is generation context only.
"""

from __future__ import annotations

import asyncio

from ccl.data import ContractService
from ccl.providers import RuleAwareStubProvider
from ccl.providers.anthropic import AnthropicProvider
from ccl.providers.base import GenerateRequest
from ccl.tutor.orchestrator import SessionContext, TutorOrchestrator
from tests.conftest import make_valid_contract


class SpyProvider:
    """Records each GenerateRequest, then delegates to the compliant stub."""

    def __init__(self):
        self.requests = []
        self.inner = RuleAwareStubProvider()

    async def generate(self, req):
        self.requests.append(req)
        return await self.inner.generate(req)

    async def embed(self, texts):
        return await self.inner.embed(texts)

    def data_policy(self):
        return self.inner.data_policy()

    def capabilities(self):
        return self.inner.capabilities()


def _publish(repo, audit):
    cs = ContractService(repo, audit)
    cs.create_draft(make_valid_contract())
    cs.validate("cc_math51_unit3_v1")
    cs.approve("cc_math51_unit3_v1", approved_by="t1")
    cs.publish("cc_math51_unit3_v1", actor_id="t1")
    return cs.get_published("math_demo")


def test_second_turn_sees_first_turn(repo, audit, seeded_material):
    contract = _publish(repo, audit)
    spy = SpyProvider()
    orch = TutorOrchestrator(repo, audit, spy)
    ctx = SessionContext(tenant_id="school_demo", course_id="math_demo", student_id="stu_1",
                         mode="practice", concept_ids=["concept_vectors"], current_unit="unit_3")

    asyncio.run(orch.respond(contract, ctx, "How do I start this vector problem?"))
    # ctx.session_id is now set; the second turn continues the same session.
    asyncio.run(orch.respond(contract, ctx, "okay, what's the next step?"))

    first_req_history = spy.requests[0].history
    second_req_history = spy.requests[1].history
    assert first_req_history == []  # nothing before the first turn
    texts = [h["text"] for h in second_req_history]
    assert "How do I start this vector problem?" in texts  # remembers the prior question
    assert any(h["role"] == "tutor" for h in second_req_history)  # and the prior reply


def test_anthropic_builds_alternating_messages():
    p = AnthropicProvider(api_key="x")  # no network call in _messages
    req = GenerateRequest(
        mode="practice", student_message="next step?", student_attempt="",
        target_hint_level=3, required_method_ids=[], forbidden_method_ids=[],
        history=[{"role": "student", "text": "how do I start?"},
                 {"role": "tutor", "text": "try writing the balance equations"}],
    )
    msgs = p._messages(req)
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user"]  # history + current, alternating
    assert msgs[0]["content"] == "how do I start?"
    assert isinstance(msgs[-1]["content"], str)  # current turn carries the payload


def test_leading_assistant_history_is_dropped():
    p = AnthropicProvider(api_key="x")
    req = GenerateRequest(
        mode="practice", student_message="q", student_attempt="",
        target_hint_level=3, required_method_ids=[], forbidden_method_ids=[],
        history=[{"role": "tutor", "text": "stray assistant line"},
                 {"role": "student", "text": "real question"}],
    )
    roles = [m["role"] for m in p._messages(req)]
    assert roles[0] == "user"  # must start with a user turn for the API
