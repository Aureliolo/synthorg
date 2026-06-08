"""Unit tests for the LLM-based semantic analyzer."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from synthorg.engine.workspace.enums import ConflictType
from synthorg.engine.workspace.models import Workspace
from synthorg.engine.workspace.semantic_llm import LlmSemanticAnalyzer
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.providers.protocol import CompletionProvider

pytestmark = pytest.mark.unit


def _make_workspace() -> Workspace:
    return Workspace(
        workspace_id="ws-1",
        task_id="task-1",
        agent_id="agent-1",
        branch_name="ws/ws-1",
        worktree_path="/tmp/ws",  # noqa: S108
        base_branch="main",
        created_at=datetime.now(tz=UTC),
    )


def _make_provider_response(
    *,
    conflicts: list[dict[str, str]] | None = None,
) -> CompletionResponse:
    """Build a response with a submit_semantic_review tool call."""
    # ``ToolCall.arguments`` is ``dict[str, JsonValue]``; a precise
    # ``list[dict[str, str]]`` is not assignable to ``list[JsonValue]``
    # under dict invariance, so cast at this embed site rather than
    # weakening the helper's parameter type.
    tc = ToolCall(
        id="tc-1",
        name="submit_semantic_review",
        arguments={
            "conflicts": cast("list[JsonValue]", conflicts or []),
            "summary": f"Found {len(conflicts or [])} conflict(s)",
        },
    )
    return CompletionResponse(
        content="",
        tool_calls=(tc,),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=100, output_tokens=50, cost=0.0),
        model="test-model",
    )


class TestLlmSemanticAnalyzer:
    """Tests for the LLM semantic analyzer."""

    def test_rejects_blank_model(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        with pytest.raises(ValueError, match="non-blank"):
            LlmSemanticAnalyzer(provider=provider, model="")

    async def test_returns_conflicts_from_llm(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete.return_value = _make_provider_response(
            conflicts=[
                {
                    "file_path": "utils.py",
                    "description": "Function renamed but still called",
                },
            ],
        )

        analyzer = LlmSemanticAnalyzer(
            provider=provider,
            model="test-medium-001",
        )

        merged_content = "def new_func():\n    pass\n"

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("utils.py",),
            base_sources={"utils.py": "def old_func():\n    pass\n"},
            merged_sources={"utils.py": merged_content},
        )

        assert len(result) == 1
        assert result[0].file_path == "utils.py"
        assert result[0].conflict_type == ConflictType.SEMANTIC
        provider.complete.assert_called_once()

    async def test_returns_empty_on_no_conflicts(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete.return_value = _make_provider_response(conflicts=[])

        analyzer = LlmSemanticAnalyzer(
            provider=provider,
            model="test-medium-001",
        )

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("foo.py",),
            base_sources={},
            merged_sources={"foo.py": "x = 1\n"},
        )

        assert result == ()

    async def test_returns_empty_on_no_matching_files(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        analyzer = LlmSemanticAnalyzer(
            provider=provider,
            model="test-medium-001",
        )

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("readme.md",),
            base_sources={},
            merged_sources={"readme.md": "# Readme\n"},
        )

        assert result == ()
        provider.complete.assert_not_called()

    async def test_returns_empty_on_provider_error(self) -> None:
        provider = AsyncMock(spec=CompletionProvider)
        provider.complete.side_effect = RuntimeError("provider down")

        analyzer = LlmSemanticAnalyzer(
            provider=provider,
            model="test-medium-001",
        )

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("foo.py",),
            base_sources={},
            merged_sources={"foo.py": "x = 1\n"},
        )

        assert result == ()

    async def test_retries_on_parse_failure(self) -> None:
        """First call returns unparseable, second returns valid."""
        bad_response = CompletionResponse(
            content="not valid json at all",
            tool_calls=(),
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
            model="test-model",
        )
        good_response = _make_provider_response(
            conflicts=[
                {"file_path": "a.py", "description": "found issue"},
            ],
        )

        provider = AsyncMock(spec=CompletionProvider)
        provider.complete.side_effect = [bad_response, good_response]

        analyzer = LlmSemanticAnalyzer(
            provider=provider,
            model="test-medium-001",
        )

        result = await analyzer.analyze(
            workspace=_make_workspace(),
            changed_files=("a.py",),
            base_sources={},
            merged_sources={"a.py": "x = 1\n"},
        )

        assert len(result) == 1
        assert provider.complete.call_count == 2
