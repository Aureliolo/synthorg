"""Tests for provider-layer enumerations."""

import pytest

from synthorg.providers.enums import FinishReason, MessageRole, StreamEventType

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMessageRole:
    """Tests for MessageRole enum."""

    def test_all_members_exist(self) -> None:
        members = set(MessageRole)
        assert len(members) == 4
        assert MessageRole.SYSTEM in members
        assert MessageRole.USER in members
        assert MessageRole.ASSISTANT in members
        assert MessageRole.TOOL in members

    def test_values_are_strings(self) -> None:
        assert MessageRole.SYSTEM.value == "system"
        assert MessageRole.USER.value == "user"
        assert MessageRole.ASSISTANT.value == "assistant"
        assert MessageRole.TOOL.value == "tool"

    def test_membership(self) -> None:
        assert "system" in MessageRole.__members__.values()
        assert "tool" in MessageRole.__members__.values()

    def test_is_str_subclass(self) -> None:
        assert isinstance(MessageRole.USER, str)


@pytest.mark.unit
class TestFinishReason:
    """Tests for FinishReason enum."""

    def test_all_members_exist(self) -> None:
        members = set(FinishReason)
        assert len(members) == 5
        assert FinishReason.STOP in members
        assert FinishReason.MAX_TOKENS in members
        assert FinishReason.TOOL_USE in members
        assert FinishReason.CONTENT_FILTER in members
        assert FinishReason.ERROR in members

    def test_values_are_strings(self) -> None:
        assert FinishReason.STOP.value == "stop"
        assert FinishReason.MAX_TOKENS.value == "max_tokens"
        assert FinishReason.TOOL_USE.value == "tool_use"
        assert FinishReason.CONTENT_FILTER.value == "content_filter"
        assert FinishReason.ERROR.value == "error"

    def test_is_str_subclass(self) -> None:
        assert isinstance(FinishReason.STOP, str)


@pytest.mark.unit
class TestStreamEventType:
    """Tests for StreamEventType enum."""

    def test_all_members_exist(self) -> None:
        members = set(StreamEventType)
        assert len(members) == 5
        assert StreamEventType.CONTENT_DELTA in members
        assert StreamEventType.TOOL_CALL_DELTA in members
        assert StreamEventType.USAGE in members
        assert StreamEventType.ERROR in members
        assert StreamEventType.DONE in members

    def test_values_are_strings(self) -> None:
        assert StreamEventType.CONTENT_DELTA.value == "content_delta"
        assert StreamEventType.TOOL_CALL_DELTA.value == "tool_call_delta"
        assert StreamEventType.USAGE.value == "usage"
        assert StreamEventType.ERROR.value == "error"
        assert StreamEventType.DONE.value == "done"

    def test_is_str_subclass(self) -> None:
        assert isinstance(StreamEventType.DONE, str)
