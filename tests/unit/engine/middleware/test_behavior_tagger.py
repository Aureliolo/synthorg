"""Tests for BehaviorTaggerMiddleware."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from synthorg.engine.loop_protocol import BehaviorTag
from synthorg.engine.middleware.behavior_tagger import (
    _DEFAULT_TOOL_TAG_MAP,
    BehaviorTaggerMiddleware,
)
from synthorg.engine.middleware.models import AgentMiddlewareContext


def _make_ctx(**metadata: Any) -> MagicMock:
    """Build a minimal mock AgentMiddlewareContext."""
    ctx = MagicMock()
    ctx.agent_id = "test-agent"
    ctx.task_id = "test-task"
    ctx.metadata = dict(metadata)
    ctx.with_metadata.side_effect = lambda k, v: _make_ctx(
        **{**metadata, k: v},
    )
    return ctx


@pytest.mark.unit
class TestBehaviorTaggerInit:
    """BehaviorTaggerMiddleware construction."""

    def test_default_name(self) -> None:
        mw = BehaviorTaggerMiddleware()
        assert mw.name == "behavior_tagger"

    def test_default_tool_tag_map(self) -> None:
        mw = BehaviorTaggerMiddleware()
        assert mw._tool_tag_map == _DEFAULT_TOOL_TAG_MAP

    def test_custom_tool_tag_map(self) -> None:
        custom = {"my_tool": BehaviorTag.MEMORY}
        mw = BehaviorTaggerMiddleware(tool_tag_map=custom)
        assert mw._tool_tag_map == custom

    def test_extra_kwargs_ignored(self) -> None:
        mw = BehaviorTaggerMiddleware(some_dep=object())
        assert mw.name == "behavior_tagger"


@pytest.mark.unit
class TestMatchTool:
    """BehaviorTaggerMiddleware._match_tool()."""

    def test_exact_match(self) -> None:
        mw = BehaviorTaggerMiddleware()
        assert mw._match_tool("read_file") == BehaviorTag.FILE_OPERATIONS

    def test_namespace_prefix_match(self) -> None:
        mw = BehaviorTaggerMiddleware()
        assert mw._match_tool("mcp__server__read_file") == BehaviorTag.FILE_OPERATIONS

    def test_no_match(self) -> None:
        mw = BehaviorTaggerMiddleware()
        assert mw._match_tool("unknown_tool") is None

    def test_no_match_with_namespace(self) -> None:
        mw = BehaviorTaggerMiddleware()
        assert mw._match_tool("mcp__server__unknown") is None


@pytest.mark.unit
class TestInferTags:
    """BehaviorTaggerMiddleware._infer_tags()."""

    def test_tool_calls_infer_file_operations(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx(pending_tool_calls=("read_file", "write_file"))
        tags = mw._infer_tags(ctx)
        assert BehaviorTag.FILE_OPERATIONS in tags

    def test_mixed_tools_infer_multiple_tags(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx(pending_tool_calls=("read_file", "web_search"))
        tags = mw._infer_tags(ctx)
        assert BehaviorTag.FILE_OPERATIONS in tags
        assert BehaviorTag.RETRIEVAL in tags

    def test_unknown_tools_infer_tool_use(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx(pending_tool_calls=("custom_tool",))
        tags = mw._infer_tags(ctx)
        assert BehaviorTag.TOOL_USE in tags

    def test_no_tools_short_output_infer_conversation(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx(output_tokens=50)
        tags = mw._infer_tags(ctx)
        assert BehaviorTag.CONVERSATION in tags

    def test_no_tools_long_output_infer_summarization(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx(output_tokens=600)
        tags = mw._infer_tags(ctx)
        assert BehaviorTag.SUMMARIZATION in tags

    def test_no_tools_no_metadata_infer_conversation(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx()
        tags = mw._infer_tags(ctx)
        assert BehaviorTag.CONVERSATION in tags

    def test_tags_are_sorted(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx(
            pending_tool_calls=("web_search", "read_file"),
        )
        tags = mw._infer_tags(ctx)
        values = [t.value for t in tags]
        assert values == sorted(values)

    def test_tags_are_deduplicated(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx(
            pending_tool_calls=("read_file",),
            tool_calls_made=("read_file",),
        )
        tags = mw._infer_tags(ctx)
        assert len(set(tags)) == len(tags)


@pytest.mark.unit
class TestAfterModel:
    """BehaviorTaggerMiddleware.after_model()."""

    async def test_attaches_tags_to_metadata(self) -> None:
        mw = BehaviorTaggerMiddleware()
        ctx = _make_ctx(pending_tool_calls=("read_file",))
        await mw.after_model(ctx)
        ctx.with_metadata.assert_called_once()
        call_args = ctx.with_metadata.call_args
        assert call_args[0][0] == "behavior_tags"
        assert BehaviorTag.FILE_OPERATIONS in call_args[0][1]

    async def test_returns_original_on_exception(self) -> None:
        mw = BehaviorTaggerMiddleware()
        # AgentMiddlewareContext is a Pydantic BaseModel; create_autospec
        # (and therefore mock_of[T]) does not expose Pydantic Field()
        # annotations as settable attributes, so the looser MagicMock
        # spec is the right rung here for an attribute-bag stand-in.
        ctx = MagicMock(spec=AgentMiddlewareContext)
        ctx.agent_id = "test-agent"
        ctx.task_id = "test-task"
        ctx.metadata = None  # Will cause AttributeError in _infer_tags
        result = await mw.after_model(ctx)
        assert result is ctx

    async def test_fallback_to_conversation_tag(self) -> None:
        mw = BehaviorTaggerMiddleware()
        # No tool calls -> falls back to CONVERSATION tag.
        ctx = _make_ctx()
        await mw.after_model(ctx)
        ctx.with_metadata.assert_called_once()
