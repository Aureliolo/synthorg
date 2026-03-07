"""Unit tests for communication error hierarchy."""

import pytest

from ai_company.communication.errors import (
    ChannelAlreadyExistsError,
    ChannelNotFoundError,
    CommunicationError,
    MessageBusAlreadyRunningError,
    MessageBusNotRunningError,
    NotSubscribedError,
)


class TestCommunicationError:
    """Tests for the base CommunicationError."""

    @pytest.mark.unit
    def test_message_stored(self) -> None:
        err = CommunicationError("something broke")
        assert err.message == "something broke"

    @pytest.mark.unit
    def test_empty_context_by_default(self) -> None:
        err = CommunicationError("fail")
        assert dict(err.context) == {}

    @pytest.mark.unit
    def test_context_is_immutable(self) -> None:
        err = CommunicationError("fail", context={"key": "val"})
        with pytest.raises(TypeError):
            err.context["new"] = "nope"  # type: ignore[index]

    @pytest.mark.unit
    def test_str_without_context(self) -> None:
        err = CommunicationError("plain")
        assert str(err) == "plain"

    @pytest.mark.unit
    def test_str_with_context(self) -> None:
        err = CommunicationError("fail", context={"channel": "#eng"})
        assert "fail" in str(err)
        assert "channel='#eng'" in str(err)

    @pytest.mark.unit
    def test_context_dict_is_copied(self) -> None:
        ctx = {"a": "1"}
        err = CommunicationError("fail", context=ctx)
        ctx["b"] = "2"
        assert "b" not in err.context


class TestSubclasses:
    """Tests for error subclasses."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("cls", "base"),
        [
            (ChannelNotFoundError, CommunicationError),
            (ChannelAlreadyExistsError, CommunicationError),
            (NotSubscribedError, CommunicationError),
            (MessageBusNotRunningError, CommunicationError),
            (MessageBusAlreadyRunningError, CommunicationError),
        ],
    )
    def test_inherits_base(self, cls: type, base: type) -> None:
        assert issubclass(cls, base)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "cls",
        [
            ChannelNotFoundError,
            ChannelAlreadyExistsError,
            NotSubscribedError,
            MessageBusNotRunningError,
            MessageBusAlreadyRunningError,
        ],
    )
    def test_subclass_carries_context(self, cls: type) -> None:
        err = cls("msg", context={"k": "v"})
        assert err.context["k"] == "v"
        assert isinstance(err, CommunicationError)
