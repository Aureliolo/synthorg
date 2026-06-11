"""Unit tests for LLM semantic analysis prompt building and parsing."""

from typing import cast

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.workspace.enums import ConflictType
from synthorg.engine.workspace.semantic_llm_prompt import (
    build_review_message,
    build_semantic_review_tool,
    build_system_message,
    parse_tool_call_response,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


class TestBuildSemanticReviewTool:
    """Tests for tool definition construction."""

    def test_returns_tool_definition(self) -> None:
        tool = build_semantic_review_tool()
        assert tool.name == "submit_semantic_review"
        schema = cast(JsonDict, tool.parameters_schema)
        assert "conflicts" in schema["properties"]
        assert "summary" in schema["properties"]

    def test_conflict_schema_has_required_fields(self) -> None:
        tool = build_semantic_review_tool()
        schema = cast(JsonDict, tool.parameters_schema)
        conflict_schema = schema["properties"]["conflicts"]["items"]
        assert "file_path" in conflict_schema["properties"]
        assert "description" in conflict_schema["properties"]
        assert set(conflict_schema["required"]) == {"file_path", "description"}


class TestBuildSystemMessage:
    """Tests for system message construction."""

    def test_returns_system_role(self) -> None:
        msg = build_system_message()
        assert msg.role == MessageRole.SYSTEM

    def test_contains_review_instructions(self) -> None:
        msg = build_system_message()
        assert msg.content is not None
        assert "semantic conflicts" in msg.content.lower()
        assert "submit_semantic_review" in msg.content


class TestBuildReviewMessage:
    """Tests for review message construction."""

    def test_includes_diff_summary(self) -> None:
        msg = build_review_message(
            diff_summary="MODIFIED: foo.py",
            changed_files={"foo.py": "x = 1"},
        )
        assert msg.role == MessageRole.USER
        assert msg.content is not None
        assert "MODIFIED: foo.py" in msg.content

    def test_includes_file_contents(self) -> None:
        msg = build_review_message(
            diff_summary="test",
            changed_files={"bar.py": "def bar():\n    pass"},
        )
        assert msg.content is not None
        assert "bar.py" in msg.content
        assert "def bar()" in msg.content

    def test_multiple_files(self) -> None:
        msg = build_review_message(
            diff_summary="test",
            changed_files={"a.py": "a = 1", "b.py": "b = 2"},
        )
        assert msg.content is not None
        assert "a.py" in msg.content
        assert "b.py" in msg.content


def _make_response(
    *,
    tool_calls: list[ToolCall] | None = None,
    content: str = "",
) -> CompletionResponse:
    """Create a test completion response."""
    return CompletionResponse(
        content=content,
        tool_calls=tuple(tool_calls) if tool_calls else (),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
        model="test-model",
    )


class TestParseToolCallResponse:
    """Tests for parsing tool call responses into conflicts."""

    def test_parses_tool_call_with_conflicts(self) -> None:
        tc = ToolCall(
            id="tc-1",
            name="submit_semantic_review",
            arguments={
                "conflicts": [
                    {
                        "file_path": "utils.py",
                        "description": "Function removed but still called",
                    },
                ],
                "summary": "Found 1 conflict",
            },
        )
        response = _make_response(tool_calls=[tc])
        conflicts = parse_tool_call_response(response)
        assert len(conflicts) == 1
        assert conflicts[0].file_path == "utils.py"
        assert conflicts[0].conflict_type == ConflictType.SEMANTIC
        assert "removed" in conflicts[0].description

    def test_parses_empty_conflicts_list(self) -> None:
        tc = ToolCall(
            id="tc-1",
            name="submit_semantic_review",
            arguments={"conflicts": [], "summary": "No issues"},
        )
        response = _make_response(tool_calls=[tc])
        conflicts = parse_tool_call_response(response)
        assert conflicts == ()

    def test_fallback_to_content_json(self) -> None:
        json_content = (
            "```json\n"
            '{"conflicts": [{"file_path": "a.py", '
            '"description": "broken import"}], '
            '"summary": "found issue"}\n'
            "```"
        )
        response = _make_response(content=json_content)
        conflicts = parse_tool_call_response(response)
        assert len(conflicts) == 1
        assert conflicts[0].file_path == "a.py"

    def test_raises_on_unparseable_response(self) -> None:
        response = _make_response(content="I found some issues but no JSON")
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_tool_call_response(response)

    def test_raises_on_empty_response(self) -> None:
        response = _make_response()
        with pytest.raises(ValueError, match="No tool call"):
            parse_tool_call_response(response)

    def test_skips_invalid_conflict_items(self) -> None:
        tc = ToolCall(
            id="tc-1",
            name="submit_semantic_review",
            arguments={
                "conflicts": [
                    {"file_path": "ok.py", "description": "valid"},
                    {"file_path": "", "description": "missing path"},
                    "not a dict",
                ],
                "summary": "mixed",
            },
        )
        response = _make_response(tool_calls=[tc])
        conflicts = parse_tool_call_response(response)
        assert len(conflicts) == 1
        assert conflicts[0].file_path == "ok.py"

    def test_multiple_conflicts(self) -> None:
        tc = ToolCall(
            id="tc-1",
            name="submit_semantic_review",
            arguments={
                "conflicts": [
                    {"file_path": "a.py", "description": "issue 1"},
                    {"file_path": "b.py", "description": "issue 2"},
                ],
                "summary": "2 issues",
            },
        )
        response = _make_response(tool_calls=[tc])
        conflicts = parse_tool_call_response(response)
        assert len(conflicts) == 2
