"""Tests for DefaultTokenEstimator and PromptTokenEstimator protocol."""

import pytest

from synthorg.engine.token_estimation import (
    DefaultTokenEstimator,
    PromptTokenEstimator,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, ToolCall, ToolResult

pytestmark = pytest.mark.unit


class TestProtocolCompliance:
    """DefaultTokenEstimator satisfies the PromptTokenEstimator protocol."""

    def test_is_runtime_checkable(self) -> None:
        assert isinstance(DefaultTokenEstimator(), PromptTokenEstimator)


class TestEstimateTokens:
    """Tests for estimate_tokens (shared chars-per-token heuristic).

    Empty text estimates to 0; any non-empty text floors to at least 1
    token (``max(1, len(text) // 4)`` via ``core.text_estimation``).
    """

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", 0),
            ("ab", 1),
            ("abc", 1),
            ("abcd", 1),
            ("abcde", 1),
            ("a" * 100, 25),
            ("a" * 99, 24),
        ],
        ids=[
            "empty",
            "2-chars",
            "3-chars",
            "4-chars-exact",
            "5-chars-floor",
            "100-chars",
            "99-chars-floor",
        ],
    )
    def test_character_to_token_ratio(self, text: str, expected: int) -> None:
        estimator = DefaultTokenEstimator()
        assert estimator.estimate_tokens(text) == expected


class TestEstimateConversationTokens:
    """Tests for estimate_conversation_tokens."""

    def test_empty_tuple(self) -> None:
        estimator = DefaultTokenEstimator()
        assert estimator.estimate_conversation_tokens(()) == 0

    def test_single_user_message(self) -> None:
        estimator = DefaultTokenEstimator()
        msg = ChatMessage(role=MessageRole.USER, content="a" * 20)
        # len("a"*20) // 4 = 5, + 4 overhead = 9
        result = estimator.estimate_conversation_tokens((msg,))
        assert result == 9

    def test_none_content_uses_empty_fallback(self) -> None:
        estimator = DefaultTokenEstimator()
        # Use model_construct to bypass ChatMessage validator that
        # requires content on user/system roles -- we're testing the
        # estimator's handling of None, not ChatMessage validation.
        msg = ChatMessage.model_construct(
            role=MessageRole.USER, content=None, tool_calls=(), tool_result=None
        )
        # len("") // 4 = 0, + 4 overhead = 4
        result = estimator.estimate_conversation_tokens((msg,))
        assert result == 4

    def test_tool_result_overrides_content(self) -> None:
        estimator = DefaultTokenEstimator()
        msg = ChatMessage(
            role=MessageRole.TOOL,
            content="ignored",
            tool_result=ToolResult(
                tool_call_id="tc-1",
                content="a" * 40,
            ),
        )
        # tool_result.content ("a"*40) // 4 = 10, + 4 overhead = 14
        result = estimator.estimate_conversation_tokens((msg,))
        assert result == 14

    def test_tool_result_empty_content(self) -> None:
        estimator = DefaultTokenEstimator()
        msg = ChatMessage(
            role=MessageRole.TOOL,
            tool_result=ToolResult(
                tool_call_id="tc-1",
                content="",
            ),
        )
        # tool_result.content "" (empty) -> len("") // 4 = 0, + 4 overhead = 4
        result = estimator.estimate_conversation_tokens((msg,))
        assert result == 4

    def test_assistant_with_tool_calls(self) -> None:
        estimator = DefaultTokenEstimator()
        tc = ToolCall(
            id="call-001",
            name="search",
            arguments={"query": "test"},
        )
        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Let me search.",
            tool_calls=(tc,),
        )
        # content: len("Let me search.") = 14, 14 // 4 = 3, + 4 = 7
        # tool_call: len("call-001")//4=2, len("search")//4=1,
        #            len("{'query': 'test'}")//4 = len(str({"query":"test"}))//4
        args_str = str(tc.arguments)
        tc_tokens = len(tc.id) // 4 + len(tc.name) // 4 + len(args_str) // 4 + 4
        expected = 7 + tc_tokens
        result = estimator.estimate_conversation_tokens((msg,))
        assert result == expected

    def test_multiple_messages_sum(self) -> None:
        estimator = DefaultTokenEstimator()
        msgs = (
            ChatMessage(role=MessageRole.USER, content="a" * 8),
            ChatMessage(role=MessageRole.ASSISTANT, content="b" * 12),
        )
        # msg1: 8//4=2, +4=6;  msg2: 12//4=3, +4=7;  total=13
        result = estimator.estimate_conversation_tokens(msgs)
        assert result == 13

    def test_multiple_tool_calls_summed(self) -> None:
        estimator = DefaultTokenEstimator()
        tc1 = ToolCall(id="id01", name="fn_a", arguments={"x": 1})
        tc2 = ToolCall(id="id02", name="fn_b", arguments={"y": 2})
        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=(tc1, tc2),
        )
        # content: None -> "" -> 0 // 4 = 0, + 4 = 4
        # tc1: len("id01")//4=1, len("fn_a")//4=1,
        #      len(str({"x":1}))//4, + 4
        # tc2: similar
        tc1_tokens = (
            len(tc1.id) // 4 + len(tc1.name) // 4 + len(str(tc1.arguments)) // 4 + 4
        )
        tc2_tokens = (
            len(tc2.id) // 4 + len(tc2.name) // 4 + len(str(tc2.arguments)) // 4 + 4
        )
        expected = 4 + tc1_tokens + tc2_tokens
        result = estimator.estimate_conversation_tokens((msg,))
        assert result == expected
