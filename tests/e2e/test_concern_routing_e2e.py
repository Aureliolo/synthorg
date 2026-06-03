"""Acceptance: topic-tagged prompts reach the right role agent.

Drives the real ``ChiefOfStaffProposer`` with a real
``LlmConcernRouter`` over a single ``ScriptedProvider`` -- zero LLM
spend. Each turn is a two-call dance: the classifier call returns a
``ConcernClassification`` JSON, then the decision call returns a
``ProposeDecision`` JSON. Both calls hit the same scripted provider in
FIFO order (the routed responder falls back to the proposer's default
provider when no provider registry is wired), so a per-turn
``[classification, decision]`` script exercises the full path. The test
asserts the responding role agent and the attribution recorded on the
assistant turn for budget -> CFO, strategy -> CEO, technical -> CTO.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import ProposeArgs
from synthorg.meta.chief_of_staff.routing import LlmConcernRouter
from tests._shared.scripted_provider import ScriptedProvider, make_text_response
from tests.unit.meta.chief_of_staff.propose_fakes import (
    build_proposer,
    build_registry,
    make_identity,
)

pytestmark = pytest.mark.e2e


def _classification(*, topic: str, role: str, confidence: float) -> str:
    return f'{{"topic": "{topic}", "role": "{role}", "confidence": {confidence}}}'


_CLARIFY = (
    '{"needs_clarification": true, '
    '"clarifying_question": "Could you say more?", "proposals": []}'
)


async def _drive_routed_turn(*, topic: str, role: str, message: str) -> None:
    """Drive one routed turn and assert it reached *role*.

    Args:
        topic: Concern label the classifier emits.
        role: Role the classifier picks (and the active agent to reach).
        message: The human turn text.
    """
    identities = {
        "CFO": make_identity(name="Casey", role="CFO"),
        "CEO": make_identity(name="Dana", role="CEO"),
        "CTO": make_identity(name="Tomas", role="CTO"),
    }
    registry = await build_registry(*identities.values())
    provider = ScriptedProvider(
        responses=[
            make_text_response(
                _classification(topic=topic, role=role, confidence=0.95)
            ),
            make_text_response(_CLARIFY),
        ]
    )
    router = LlmConcernRouter(
        provider=provider,
        model=NotBlankStr("test-model-001"),
        agent_registry=registry,
        confidence_floor=0.6,
        default_role=NotBlankStr("CEO"),
        temperature=0.0,
        max_tokens=200,
        timeout_seconds=120.0,
    )
    proposer, _, turn_repo, _, _ = build_proposer(
        provider=provider,
        config=ChiefOfStaffConfig(propose_enabled=True, routing_enabled=True),
        role_router=router,
    )

    result = await proposer.converse(
        ProposeArgs(message=NotBlankStr(message), created_by=NotBlankStr("user-1"))
    )

    assert result.responder_role == role
    assert result.routed_topic == topic
    # The two scripted responses were both consumed: classify, then decide.
    assert provider.call_count == 2
    # The assistant turn is attributed to the routed role agent, and the
    # decision prompt is voiced in that role (not the generic CoS).
    assistant = next(t for t in turn_repo.turns if t.role.value == "assistant")
    assert assistant.routed_topic == topic
    assert assistant.author_agent_id == str(identities[role].id)
    decision_prompt = provider.received_messages[1][0].content or ""
    assert f"a {role} in" in decision_prompt
    assert "You are the Chief of Staff." not in decision_prompt


class TestConcernRoutingE2E:
    @pytest.mark.parametrize(
        ("topic", "role", "message"),
        [
            ("budget", "CFO", "What is our Q3 budget burn?"),
            ("strategy", "CEO", "What should our market strategy be next year?"),
            ("technical", "CTO", "Is our architecture ready to scale?"),
        ],
        ids=["budget->CFO", "strategy->CEO", "technical->CTO"],
    )
    async def test_topic_routes_to_role(
        self, topic: str, role: str, message: str
    ) -> None:
        await _drive_routed_turn(topic=topic, role=role, message=message)
