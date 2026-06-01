"""Integration tests: concern routing in front of the propose loop."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import ConversationKind
from synthorg.meta.chief_of_staff.models import ProposeArgs
from synthorg.meta.chief_of_staff.responder import (
    generic_responder,
    resolve_responder_provider,
    responder_for_identity,
)
from synthorg.meta.chief_of_staff.routing import KeywordRoleRouter
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.registry import ProviderRegistry
from tests._shared import mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_text_response
from tests.unit.meta.chief_of_staff.propose_fakes import (
    build_proposer,
    build_registry,
    make_identity,
)

pytestmark = pytest.mark.unit

_CLARIFY_JSON = (
    '{"needs_clarification": true, '
    '"clarifying_question": "Which budget line?", '
    '"proposals": []}'
)
_PROPOSE_JSON = (
    '{"needs_clarification": false, "clarifying_question": null, '
    '"proposals": [{"title": "Trim cloud spend", '
    '"raw_intent": "Reduce monthly cloud cost by 20%", '
    '"project": "finance", "priority": "high", '
    '"task_type": "research", "estimated_complexity": "medium", '
    '"acceptance_criteria": ["cost report"]}]}'
)


async def _keyword_router(*, cfo_name: str = "Casey") -> KeywordRoleRouter:
    registry = await build_registry(make_identity(name=cfo_name, role="CFO"))
    return KeywordRoleRouter(agent_registry=registry, default_role=NotBlankStr("CEO"))


class TestRoutedClarification:
    async def test_routed_clarification_answers_as_role_agent(self) -> None:
        provider = ScriptedProvider(responses=[make_text_response(_CLARIFY_JSON)])
        proposer, conv_repo, turn_repo, _, _ = build_proposer(
            provider=provider, role_router=await _keyword_router()
        )

        result = await proposer.converse(
            ProposeArgs(
                message=NotBlankStr("What is our cloud budget this quarter?"),
                created_by=NotBlankStr("user-1"),
            )
        )

        assert result.status == "needs_clarification"
        assert result.responder_role == "CFO"
        assert result.responder_name == "Casey"
        assert result.routed_topic == "budget"
        assert result.routing_confidence == pytest.approx(1.0)

        # The decision prompt is voiced as the CFO, not the generic CoS.
        prompt = provider.received_messages[0][0].content or ""
        assert "a CFO in" in prompt
        assert "You are the Chief of Staff." not in prompt

        # The conversation is marked routed and the assistant turn carries
        # the role-agent attribution columns.
        conv = conv_repo.items[result.conversation_id]
        assert conv.kind is ConversationKind.ROUTED
        assistant = next(t for t in turn_repo.turns if t.role.value == "assistant")
        assert assistant.author_name == "Casey"
        assert assistant.author_agent_id is not None
        assert assistant.routed_topic == "budget"
        assert assistant.routing_confidence == pytest.approx(1.0)


class TestRoutedProposal:
    async def test_routed_proposal_records_attribution(self) -> None:
        provider = ScriptedProvider(responses=[make_text_response(_PROPOSE_JSON)])
        proposer, conv_repo, turn_repo, _, _ = build_proposer(
            provider=provider, role_router=await _keyword_router()
        )

        result = await proposer.converse(
            ProposeArgs(
                message=NotBlankStr("Cut our cloud spend"),
                created_by=NotBlankStr("user-1"),
            )
        )

        assert result.status == "proposed"
        assert result.responder_role == "CFO"
        # "spend" is the CFO budget keyword present in the message.
        assert result.routed_topic == "spend"
        conv = conv_repo.items[result.conversation_id]
        assert conv.kind is ConversationKind.ROUTED
        summary = next(t for t in turn_repo.turns if t.role.value == "assistant")
        assert summary.author_name == "Casey"
        assert summary.routed_topic == "spend"


class TestRoutingDisabled:
    async def test_no_router_keeps_generic_chief_of_staff(self) -> None:
        provider = ScriptedProvider(responses=[make_text_response(_CLARIFY_JSON)])
        proposer, conv_repo, turn_repo, _, _ = build_proposer(provider=provider)

        result = await proposer.converse(
            ProposeArgs(
                message=NotBlankStr("What is our cloud budget?"),
                created_by=NotBlankStr("user-1"),
            )
        )

        assert result.responder_role is None
        assert result.routed_topic is None
        prompt = provider.received_messages[0][0].content or ""
        assert "You are the Chief of Staff." in prompt
        conv = conv_repo.items[result.conversation_id]
        assert conv.kind is ConversationKind.DIRECT
        assistant = next(t for t in turn_repo.turns if t.role.value == "assistant")
        assert assistant.author_agent_id is None
        assert assistant.routed_topic is None

    async def test_no_keyword_match_stays_generic(self) -> None:
        provider = ScriptedProvider(responses=[make_text_response(_CLARIFY_JSON)])
        proposer, conv_repo, _, _, _ = build_proposer(
            provider=provider, role_router=await _keyword_router()
        )

        result = await proposer.converse(
            ProposeArgs(
                message=NotBlankStr("Tell me a joke please"),
                created_by=NotBlankStr("user-1"),
            )
        )

        assert result.responder_role is None
        conv = conv_repo.items[result.conversation_id]
        assert conv.kind is ConversationKind.DIRECT


class TestProviderResolution:
    def test_routed_responder_resolves_agent_provider(self) -> None:
        default_provider = ScriptedProvider(responses=[])
        agent_provider = mock_of[BaseCompletionProvider]()
        registry = ProviderRegistry({"cfo-provider": agent_provider})
        cfo = make_identity(name="Casey", role="CFO", provider="cfo-provider")

        resolved = resolve_responder_provider(
            responder_for_identity(cfo),
            default=default_provider,
            registry=registry,
        )

        assert resolved is agent_provider

    def test_generic_responder_uses_default_provider(self) -> None:
        default_provider = ScriptedProvider(responses=[])
        registry = ProviderRegistry({"cfo-provider": mock_of[BaseCompletionProvider]()})

        resolved = resolve_responder_provider(
            generic_responder(model=NotBlankStr("propose-model")),
            default=default_provider,
            registry=registry,
        )

        assert resolved is default_provider
