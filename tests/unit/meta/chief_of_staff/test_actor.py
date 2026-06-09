"""Unit tests for the ConversationalActor (direct MCP acting).

The actor is a thin wrapper: it resolves the acting agent + effective
autonomy and delegates to ``AgentEngine.run_chat_action``. These tests
pin the resolution + delegation + result shaping; the governed
tool-execution / park / resume behaviour is covered by
``tests/unit/engine/test_run_chat_action.py``.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, ToolPermissions
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.chat_action import ChatActionResult, ExecutedToolCall
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.meta.chief_of_staff.actor import (
    ConversationalActArgs,
    ConversationalActor,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.security.autonomy.models import EffectiveAutonomy
from synthorg.security.autonomy.resolver import AutonomyResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_AGENT_ID = uuid4()


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=_AGENT_ID,
        name="Casey",
        role="CFO",
        department="Finance",
        level=SeniorityLevel.SENIOR,
        autonomy_level=AutonomyLevel.SUPERVISED,
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        tools=ToolPermissions(access_level=ToolAccessLevel.STANDARD),
    )


def _completed_result() -> ChatActionResult:
    return ChatActionResult(
        termination_reason=TerminationReason.COMPLETED,
        final_message="Done.",
        tool_calls=(
            ExecutedToolCall(tool_name="query_metrics", is_error=False, result="ok"),
        ),
    )


def _parked_result() -> ChatActionResult:
    return ChatActionResult(
        termination_reason=TerminationReason.PARKED,
        approval_id="appr-act-1",
    )


def _actor(
    *,
    result: ChatActionResult,
    identity: AgentIdentity | None = None,
    by_name: bool = False,
    autonomy_resolver: AutonomyResolver | None = None,
) -> tuple[ConversationalActor, MagicMock]:
    engine = mock_of[AgentEngine](run_chat_action=AsyncMock(return_value=result))
    registry = mock_of[AgentRegistryService](
        get=AsyncMock(return_value=None if by_name else identity),
        get_by_name=AsyncMock(return_value=identity if by_name else None),
    )
    actor = ConversationalActor(
        engine=engine,
        agent_registry=registry,
        autonomy_resolver=autonomy_resolver,
        config=ChiefOfStaffConfig(direct_mcp_enabled=True, direct_mcp_max_turns=4),
    )
    return actor, engine


class TestConversationalActor:
    async def test_resolves_by_id_and_delegates(self) -> None:
        identity = _identity()
        actor, engine = _actor(result=_completed_result(), identity=identity)

        result = await actor.act(
            ConversationalActArgs(instruction="check revenue", agent=str(_AGENT_ID)),
        )

        assert result.agent_id == str(_AGENT_ID)
        assert result.agent_name == "Casey"
        assert result.action.final_message == "Done."
        engine.run_chat_action.assert_awaited_once()
        kwargs = engine.run_chat_action.await_args.kwargs
        assert kwargs["identity"] is identity
        assert kwargs["instruction"] == "check revenue"
        assert kwargs["max_turns"] == 4

    async def test_resolves_by_name_when_id_misses(self) -> None:
        identity = _identity()
        actor, engine = _actor(
            result=_completed_result(), identity=identity, by_name=True
        )

        result = await actor.act(
            ConversationalActArgs(instruction="check revenue", agent="Casey"),
        )

        assert result.agent_name == "Casey"
        engine.run_chat_action.assert_awaited_once()

    async def test_unknown_agent_raises_not_found(self) -> None:
        actor, engine = _actor(result=_completed_result(), identity=None)

        with pytest.raises(NotFoundError):
            await actor.act(
                ConversationalActArgs(instruction="do it", agent="ghost"),
            )
        engine.run_chat_action.assert_not_awaited()

    async def test_parked_result_surfaces_approval_id(self) -> None:
        actor, _ = _actor(result=_parked_result(), identity=_identity())

        result = await actor.act(
            ConversationalActArgs(instruction="deploy", agent=str(_AGENT_ID)),
        )

        assert result.action.parked
        assert result.action.approval_id == "appr-act-1"

    async def test_resolves_autonomy_from_resolver(self) -> None:
        identity = _identity()
        sentinel = mock_of[EffectiveAutonomy]()
        resolver = mock_of[AutonomyResolver](
            resolve=MagicMock(return_value=sentinel),
        )
        actor, engine = _actor(
            result=_completed_result(),
            identity=identity,
            autonomy_resolver=resolver,
        )

        await actor.act(
            ConversationalActArgs(instruction="check", agent=str(_AGENT_ID)),
        )

        resolver.resolve.assert_called_once_with(
            agent_level=identity.autonomy_level,
            seniority=identity.level,
        )
        passed = engine.run_chat_action.await_args.kwargs["effective_autonomy"]
        assert passed is sentinel

    async def test_autonomy_resolution_failure_degrades_to_none(self) -> None:
        identity = _identity()
        resolver = mock_of[AutonomyResolver](
            resolve=MagicMock(side_effect=ValueError("seniority violation")),
        )
        actor, engine = _actor(
            result=_completed_result(),
            identity=identity,
            autonomy_resolver=resolver,
        )

        await actor.act(
            ConversationalActArgs(instruction="check", agent=str(_AGENT_ID)),
        )

        assert engine.run_chat_action.await_args.kwargs["effective_autonomy"] is None
