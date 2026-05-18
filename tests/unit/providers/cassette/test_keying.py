"""Unit tests for cassette request keying.

The replay key is a SHA-256 over the canonicalised request. Two
requests replay to the same recorded interaction iff their key is
identical, so these tests pin exactly which request dimensions
participate in the hash and which do not.
"""

import pytest

from synthorg.providers.cassette.keying import (
    CassetteMethod,
    CassetteRequestKey,
    request_hash,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    ToolCall,
    ToolDefinition,
)

pytestmark = pytest.mark.unit

_HEX_DIGEST_LEN = 64


def _msgs(text: str = "Hello") -> tuple[ChatMessage, ...]:
    return (ChatMessage(role=MessageRole.USER, content=text),)


class TestRequestHashStability:
    """Identical request inputs always produce the identical hash."""

    def test_same_inputs_same_hash(self) -> None:
        a = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        b = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        assert a == b

    def test_hash_is_lowercase_hex_sha256(self) -> None:
        digest = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        assert len(digest) == _HEX_DIGEST_LEN
        assert digest == digest.lower()
        int(digest, 16)  # raises if not hex

    def test_tool_argument_key_order_independent(self) -> None:
        """dict key ordering must not change the hash (canonical JSON)."""
        m1 = (
            ChatMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=(ToolCall(id="c1", name="t", arguments={"a": 1, "b": 2}),),
            ),
        )
        m2 = (
            ChatMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=(ToolCall(id="c1", name="t", arguments={"b": 2, "a": 1}),),
            ),
        )
        h1 = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=m1,
            tools=(),
            config=None,
        )
        h2 = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=m2,
            tools=(),
            config=None,
        )
        assert h1 == h2


class TestRequestHashSensitivity:
    """Every request dimension that affects the response affects the hash."""

    def test_message_content_changes_hash(self) -> None:
        base = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs("one"),
            tools=(),
            config=None,
        )
        other = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs("two"),
            tools=(),
            config=None,
        )
        assert base != other

    def test_message_order_changes_hash(self) -> None:
        a = (
            ChatMessage(role=MessageRole.USER, content="first"),
            ChatMessage(role=MessageRole.USER, content="second"),
        )
        b = (
            ChatMessage(role=MessageRole.USER, content="second"),
            ChatMessage(role=MessageRole.USER, content="first"),
        )
        ha = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=a,
            tools=(),
            config=None,
        )
        hb = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=b,
            tools=(),
            config=None,
        )
        assert ha != hb

    def test_model_changes_hash(self) -> None:
        a = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m1",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        b = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m2",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        assert a != b

    def test_provider_changes_hash(self) -> None:
        a = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p1",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        b = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p2",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        assert a != b

    def test_tools_change_hash(self) -> None:
        tool = ToolDefinition(name="search", description="d")
        a = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        b = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(tool,),
            config=None,
        )
        assert a != b

    def test_config_changes_hash(self) -> None:
        a = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=CompletionConfig(temperature=0.0),
        )
        b = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=CompletionConfig(temperature=1.0),
        )
        assert a != b

    def test_config_none_distinct_from_default_config(self) -> None:
        """``None`` and an explicit default config are different requests.

        A caller that passes ``CompletionConfig()`` has pinned every
        provider default explicitly; one that passes ``None`` left them
        to the provider. They are not interchangeable for replay.
        """
        none_cfg = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        default_cfg = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=CompletionConfig(),
        )
        assert none_cfg != default_cfg

    def test_method_changes_hash(self) -> None:
        """The same prompt under complete vs stream is a distinct key."""
        complete = request_hash(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        stream = request_hash(
            method=CassetteMethod.STREAM,
            provider="p",
            model="m",
            messages=_msgs(),
            tools=(),
            config=None,
        )
        assert complete != stream


class TestCassetteRequestKeyModel:
    """The key model is frozen and forbids unknown fields."""

    def test_model_is_frozen(self) -> None:
        key = CassetteRequestKey(
            method=CassetteMethod.COMPLETE,
            provider="p",
            model="m",
            messages=_msgs(),
        )
        with pytest.raises(ValueError, match="frozen"):
            key.provider = "other"  # type: ignore[misc]

    def test_capabilities_key_needs_no_messages(self) -> None:
        """Capability lookups key on provider+model+method only."""
        key = CassetteRequestKey(
            method=CassetteMethod.CAPABILITIES,
            provider="p",
            model="m",
        )
        assert key.messages == ()
        assert key.tools == ()
        assert key.config is None
