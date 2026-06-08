# mypy: disable-error-code="explicit-any"
"""Tests for the procedural memory proposer (LLM-based analysis)."""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.core.task_enums import TaskType
from synthorg.memory.procedural.models import (
    FailureAnalysisPayload,
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
)
from synthorg.memory.procedural.proposer import (
    ProceduralMemoryProposer,
    _build_user_message,
    _extract_json,
)
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_MEMORY_LOW_CONFIDENCE,
    PROCEDURAL_MEMORY_PROPOSED,
    PROCEDURAL_MEMORY_SKIPPED,
)
from synthorg.providers.enums import FinishReason
from synthorg.providers.errors import (
    AuthenticationError,
    ProviderTimeoutError,
)
from synthorg.providers.models import CompletionResponse, TokenUsage, ToolCall
from synthorg.providers.protocol import CompletionProvider


def _make_payload(**overrides: Any) -> FailureAnalysisPayload:
    defaults: dict[str, Any] = {
        "task_id": "task-001",
        "task_title": "Implement auth module",
        "task_description": "Create JWT authentication.",
        "task_type": TaskType.DEVELOPMENT,
        "error_message": "LLM timeout after 30s",
        "strategy_type": "fail_reassign",
        "termination_reason": "error",
        "turn_count": 5,
        "tool_calls_made": ("code_search", "run_tests"),
        "retry_count": 0,
        "max_retries": 2,
        "can_reassign": True,
    }
    defaults.update(overrides)
    return FailureAnalysisPayload(**defaults)


_VALID_PROPOSAL_JSON = json.dumps(
    {
        "discovery": "When facing LLM timeouts, break task into smaller steps.",
        "condition": "Task exceeds 10 turns without progress.",
        "action": "Decompose the task into subtasks before retrying.",
        "rationale": "Smaller tasks reduce context window pressure.",
        "execution_steps": ["Break task into subtasks", "Retry each subtask"],
        "confidence": 0.85,
        "tags": ["timeout", "decomposition"],
    },
)

_LOW_CONFIDENCE_JSON = json.dumps(
    {
        "discovery": "Unclear pattern.",
        "condition": "Unknown.",
        "action": "Try again.",
        "rationale": "Maybe it works.",
        "confidence": 0.2,
        "tags": [],
    },
)


def _make_response(content: str | None = _VALID_PROPOSAL_JSON) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=100, output_tokens=50, cost=0.001),
        model="test-small-001",
    )


def _make_proposer(
    response: CompletionResponse | None = None,
    *,
    side_effect: Exception | None = None,
    min_confidence: float = 0.5,
) -> tuple[ProceduralMemoryProposer, AsyncMock]:
    provider = AsyncMock(spec=CompletionProvider)
    if side_effect is not None:
        provider.complete = AsyncMock(side_effect=side_effect)
    else:
        provider.complete = AsyncMock(
            return_value=response or _make_response(),
        )
    config = ProceduralMemoryConfig(
        model="test-small-001",
        min_confidence=min_confidence,
    )
    proposer = ProceduralMemoryProposer(provider=provider, config=config)
    return proposer, provider


@pytest.mark.unit
class TestProceduralMemoryProposer:
    async def test_happy_path_returns_proposal(self) -> None:
        proposer, provider = _make_proposer()
        result = await proposer.propose(_make_payload())

        assert result is not None
        assert isinstance(result, ProceduralMemoryProposal)
        assert result.discovery.startswith("When facing")
        assert result.confidence == 0.85
        assert result.tags == ("timeout", "decomposition")
        provider.complete.assert_awaited_once()

    async def test_uses_configured_model(self) -> None:
        proposer, provider = _make_proposer()
        await proposer.propose(_make_payload())

        call_args = provider.complete.call_args
        assert call_args[0][1] == "test-small-001"

    async def test_passes_completion_config(self) -> None:
        """Provider receives the config derived from ProceduralMemoryConfig."""
        proposer, provider = _make_proposer()
        await proposer.propose(_make_payload())

        call_kwargs = provider.complete.call_args[1]
        assert "config" in call_kwargs
        assert call_kwargs["config"].temperature == 0.3
        assert call_kwargs["config"].max_tokens == 1500

    async def test_sends_system_and_user_messages(self) -> None:
        proposer, provider = _make_proposer()
        await proposer.propose(_make_payload())

        messages = provider.complete.call_args[0][0]
        assert len(messages) == 2
        assert messages[0].role.value == "system"
        assert messages[1].role.value == "user"

    async def test_user_message_contains_task_context(self) -> None:
        proposer, provider = _make_proposer()
        payload = _make_payload(task_title="Fix database migration")
        await proposer.propose(payload)

        user_msg = provider.complete.call_args[0][0][1].content
        assert "Fix database migration" in user_msg
        assert "LLM timeout after 30s" in user_msg

    async def test_user_message_has_structural_delimiters(self) -> None:
        """Fences prevent prompt injection in proposer payloads."""
        proposer, provider = _make_proposer()
        await proposer.propose(_make_payload())

        user_msg = provider.complete.call_args[0][0][1].content
        # Untrusted task fields are wrapped in ``<task-data>`` and
        # tool-call summaries in ``<tool-result>``; the system
        # prompt carries the matching directive.
        assert "<task-data>" in user_msg
        assert "</task-data>" in user_msg
        assert "<tool-result>" in user_msg
        assert "</tool-result>" in user_msg

    async def test_low_confidence_returns_none(self) -> None:
        response = _make_response(_LOW_CONFIDENCE_JSON)
        proposer, _ = _make_proposer(response, min_confidence=0.5)

        with structlog.testing.capture_logs() as logs:
            result = await proposer.propose(_make_payload())

        assert result is None
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_LOW_CONFIDENCE in events

    async def test_retryable_provider_error_returns_none(self) -> None:
        proposer, _ = _make_proposer(
            side_effect=ProviderTimeoutError("timeout"),
        )

        with structlog.testing.capture_logs() as logs:
            result = await proposer.propose(_make_payload())

        assert result is None
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_SKIPPED in events

    async def test_non_retryable_provider_error_raises(self) -> None:
        proposer, _ = _make_proposer(
            side_effect=AuthenticationError("bad key"),
        )

        with pytest.raises(AuthenticationError):
            await proposer.propose(_make_payload())

    async def test_malformed_json_returns_none(self) -> None:
        response = _make_response("not valid json {{{")
        proposer, _ = _make_proposer(response)

        with structlog.testing.capture_logs() as logs:
            result = await proposer.propose(_make_payload())

        assert result is None
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_SKIPPED in events

    async def test_empty_response_returns_none(self) -> None:
        """Provider returns content=None via tool_calls path."""
        response = CompletionResponse(
            content=None,
            finish_reason=FinishReason.TOOL_USE,
            usage=TokenUsage(input_tokens=100, output_tokens=0, cost=0.0),
            model="test-small-001",
            tool_calls=(ToolCall(id="tc-1", name="noop", arguments={}),),
        )
        proposer, _ = _make_proposer(response)

        result = await proposer.propose(_make_payload())
        assert result is None

    async def test_whitespace_response_returns_none(self) -> None:
        response = _make_response("   ")
        proposer, _ = _make_proposer(response)

        result = await proposer.propose(_make_payload())
        assert result is None

    async def test_markdown_fenced_json_parsed(self) -> None:
        fenced = f"```json\n{_VALID_PROPOSAL_JSON}\n```"
        response = _make_response(fenced)
        proposer, _ = _make_proposer(response)

        result = await proposer.propose(_make_payload())
        assert result is not None
        assert result.confidence == 0.85

    async def test_bare_markdown_fences_parsed(self) -> None:
        """Fences without the 'json' language tag are handled."""
        fenced = f"```\n{_VALID_PROPOSAL_JSON}\n```"
        response = _make_response(fenced)
        proposer, _ = _make_proposer(response)

        result = await proposer.propose(_make_payload())
        assert result is not None
        assert result.confidence == 0.85

    async def test_logs_proposed_event(self) -> None:
        proposer, _ = _make_proposer()

        with structlog.testing.capture_logs() as logs:
            await proposer.propose(_make_payload())

        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_PROPOSED in events

    async def test_generic_exception_returns_none(self) -> None:
        proposer, _ = _make_proposer(side_effect=RuntimeError("unexpected"))

        with structlog.testing.capture_logs() as logs:
            result = await proposer.propose(_make_payload())

        assert result is None
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_SKIPPED in events

    async def test_non_dict_json_returns_none(self) -> None:
        """LLM returning a JSON array instead of object is rejected."""
        response = _make_response("[1, 2, 3]")
        proposer, _ = _make_proposer(response)

        with structlog.testing.capture_logs() as logs:
            result = await proposer.propose(_make_payload())

        assert result is None
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_SKIPPED in events

    async def test_valid_json_wrong_schema_returns_none(self) -> None:
        """Valid JSON dict missing required fields is rejected."""
        response = _make_response('{"discovery": "test"}')
        proposer, _ = _make_proposer(response)

        with structlog.testing.capture_logs() as logs:
            result = await proposer.propose(_make_payload())

        assert result is None
        skipped = [e for e in logs if e["event"] == PROCEDURAL_MEMORY_SKIPPED]
        assert any(e.get("reason") == "validation_failed" for e in skipped)

    async def test_memory_error_propagates(self) -> None:
        """MemoryError is never swallowed."""
        proposer, _ = _make_proposer(side_effect=MemoryError("oom"))

        with pytest.raises(MemoryError):
            await proposer.propose(_make_payload())


@pytest.mark.unit
class TestExtractJson:
    def test_plain_json(self) -> None:
        data = _extract_json('{"key": "value"}')
        assert data == {"key": "value"}

    def test_non_dict_returns_none(self) -> None:
        assert _extract_json("[1, 2, 3]") is None

    def test_empty_string_returns_none(self) -> None:
        assert _extract_json("") is None

    def test_invalid_json_returns_none(self) -> None:
        assert _extract_json("not json") is None


@pytest.mark.unit
class TestBuildUserMessage:
    def test_contains_all_fields(self) -> None:
        payload = _make_payload()
        msg = _build_user_message(payload)

        assert "Implement auth module" in msg
        assert "Create JWT authentication." in msg
        assert "LLM timeout after 30s" in msg
        assert "fail_reassign" in msg
        assert "code_search, run_tests" in msg
        assert "Retry 0/2" in msg

    def test_empty_tools_shows_none(self) -> None:
        payload = _make_payload(tool_calls_made=())
        msg = _build_user_message(payload)

        # The empty tool list still appears under the
        # ``## Tool calls made`` heading; the literal "none" is
        # wrapped inside a ``<tool-result>`` fence.
        assert "## Tool calls made" in msg
        assert "<tool-result>\nnone\n</tool-result>" in msg
