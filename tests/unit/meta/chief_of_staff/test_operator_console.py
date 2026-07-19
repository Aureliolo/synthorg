# module-kind: tests
"""Unit tests for the operator console service.

The console is a thin wrapper over ``AgentEngine.run_chat_action`` acting as
the shared system console identity. These tests prove: a permitted configure
turn completes with console attribution; the console operating brief rides in
the system prompt; a sensitive action parks; and the per-session budget
checker trips at the cost ceiling.
"""

from typing import cast

import pytest
from pydantic import JsonValue

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.chat_action import ChatActionResult
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.integrations.connections.secret_capture import (
    PendingSecretCapture,
    SecretCaptureService,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.console_identity import build_console_identity
from synthorg.meta.chief_of_staff.operator_console import (
    CONSOLE_OPERATING_BRIEF,
    ConsoleTurnArgs,
    OperatorConsoleService,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ZERO_TOKEN_USAGE,
    CompletionResponse,
    ToolCall,
)
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.tools.registry import ToolRegistry
from tests._shared import FakeClock, InMemorySecretBackend
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.engine.chat_action_fakes import InMemoryParkedRepo, QueryTool

pytestmark = pytest.mark.unit

_MODEL = serialize_model_ref(
    ModelRef(provider="test-provider", model_id="test-model-001")
)
_APPROVAL_CALL = {
    "action_type": "deploy:service",
    "title": "Deploy to prod",
    "description": "Ship the release to production.",
}


def _tool_call(name: str, **arguments: object) -> CompletionResponse:
    return CompletionResponse(
        content=f"calling {name}",
        finish_reason=FinishReason.TOOL_USE,
        tool_calls=(
            ToolCall(
                id=f"tc-{name}",
                name=name,
                arguments=cast("dict[str, JsonValue]", arguments),
            ),
        ),
        usage=ZERO_TOKEN_USAGE,
        model="test-model-001",
    )


def _final(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=ZERO_TOKEN_USAGE,
        model="test-model-001",
    )


def _service(
    *,
    responses: list[CompletionResponse],
    config: ChiefOfStaffConfig | None = None,
    secret_capture: SecretCaptureService | None = None,
) -> tuple[OperatorConsoleService, ScriptedProvider, QueryTool]:
    provider = ScriptedProvider(responses)
    tool = QueryTool()
    engine = AgentEngine(
        provider=provider,
        tool_registry=ToolRegistry([tool]),
        approval_store=ApprovalStore(),
        parked_context_repo=InMemoryParkedRepo(),
    )
    cfg = config or ChiefOfStaffConfig(
        operator_console_enabled=True, operator_console_model=_MODEL
    )
    identity = build_console_identity(
        model_ref=_MODEL,
        autonomy_level=cfg.operator_console_autonomy_level,
        clock=FakeClock(),
    )
    assert identity is not None
    service = OperatorConsoleService(
        engine=engine,
        identity=identity,
        autonomy_resolver=None,
        config=cfg,
        secret_capture=secret_capture,
    )
    return service, provider, tool


class TestConfigure:
    async def test_permitted_configure_completes_with_attribution(self) -> None:
        service, _provider, tool = _service(
            responses=[
                _tool_call("query_metrics", window="7d"),
                _final("Connected the integration and verified health."),
            ]
        )

        result = await service.configure(
            ConsoleTurnArgs(instruction="Connect GitHub and verify it.")
        )

        assert result.action.termination_reason == TerminationReason.COMPLETED
        assert not result.action.parked
        assert result.action.final_message == (
            "Connected the integration and verified health."
        )
        assert result.console_name == "Operator Console"
        assert [tc.tool_name for tc in result.action.tool_calls] == ["query_metrics"]
        assert tool.calls == [{"window": "7d"}]

    async def test_operating_brief_rides_in_system_prompt(self) -> None:
        service, provider, _tool = _service(responses=[_final("Done.")])

        await service.configure(ConsoleTurnArgs(instruction="What is connected?"))

        first_turn = provider.received_messages[0]
        system_msg = next(m for m in first_turn if m.role == MessageRole.SYSTEM)
        assert system_msg.content is not None
        assert CONSOLE_OPERATING_BRIEF in system_msg.content

    async def test_sensitive_action_parks(self) -> None:
        service, _provider, tool = _service(
            responses=[_tool_call("request_human_approval", **_APPROVAL_CALL)]
        )

        result = await service.configure(
            ConsoleTurnArgs(instruction="Deploy the release to production.")
        )

        assert result.action.parked
        assert result.action.termination_reason == TerminationReason.PARKED
        assert result.action.approval_id is not None
        # No side effect: the gated work never ran.
        assert tool.calls == []


class TestBudgetCeiling:
    async def test_configure_threads_cost_ceiling_into_run_chat_action(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The console passes its cost ceiling to ``run_chat_action``.

        The engine derives the per-turn budget checker from the ceiling and
        carries it on the context so the bound survives a park/resume (see
        the engine's ``test_cost_ceiling`` tests); the console's own job is
        only to supply the configured ceiling.
        """
        service, _provider, _tool = _service(
            responses=[_final("noop")],
            config=ChiefOfStaffConfig(
                operator_console_enabled=True,
                operator_console_model=_MODEL,
                operator_console_cost_ceiling=0.5,
            ),
        )
        captured: dict[str, object] = {}

        async def _spy(**kwargs: object) -> ChatActionResult:
            captured.update(kwargs)
            return ChatActionResult(
                termination_reason=TerminationReason.COMPLETED,
                final_message="done",
                tool_calls=(),
            )

        monkeypatch.setattr(service._engine, "run_chat_action", _spy)
        await service.configure(
            ConsoleTurnArgs(
                instruction=NotBlankStr("connect the thing"),
                conversation_id=None,
                requested_by=None,
            )
        )

        assert captured["cost_ceiling"] == 0.5


class TestInChatCapture:
    async def test_pending_captures_surfaced_for_the_turn_draft(self) -> None:
        # A capture the console raised this turn (here pre-registered against the
        # supplied draft, standing in for the request_secret_capture tool) is
        # read back and surfaced so the dashboard renders the masked field.
        capture = SecretCaptureService(secret_backend=InMemorySecretBackend())
        capture.register_pending(
            PendingSecretCapture(
                draft_id=NotBlankStr("d1"),
                connection_type=NotBlankStr("database"),
                field_name=NotBlankStr("password"),
                secret_kind=NotBlankStr("password"),
                label=NotBlankStr("Password"),
            )
        )
        service, _provider, _tool = _service(
            responses=[_final("I need the database password.")],
            secret_capture=capture,
        )

        result = await service.configure(
            ConsoleTurnArgs(
                instruction=NotBlankStr("connect Postgres"),
                connection_draft_id=NotBlankStr("d1"),
            )
        )

        assert result.connection_draft_id == "d1"
        assert [c.field_name for c in result.pending_captures] == ["password"]
        # Consumed on read: a second turn does not re-surface it.
        assert capture.take_pending("d1") == ()

    async def test_provided_handles_and_draft_ride_in_prompt(self) -> None:
        service, provider, _tool = _service(responses=[_final("Creating it now.")])

        await service.configure(
            ConsoleTurnArgs(
                instruction=NotBlankStr("finish connecting Postgres"),
                connection_draft_id=NotBlankStr("d1"),
                provided_credential_handles={
                    NotBlankStr("password"): NotBlankStr("sech_abc123"),
                },
            )
        )

        first_turn = provider.received_messages[0]
        system_msg = next(m for m in first_turn if m.role == MessageRole.SYSTEM)
        assert system_msg.content is not None
        # The draft id and the opaque handle (not a secret) reach the prompt so
        # the console binds the create call to them.
        assert "d1" in system_msg.content
        assert "password=sech_abc123" in system_msg.content

    async def test_no_secret_capture_service_yields_no_pending(self) -> None:
        service, _provider, _tool = _service(responses=[_final("Done.")])

        result = await service.configure(
            ConsoleTurnArgs(instruction=NotBlankStr("what is connected?"))
        )

        assert result.pending_captures == ()
        assert result.connection_draft_id is not None
