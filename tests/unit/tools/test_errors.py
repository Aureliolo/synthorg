"""Tests for tool error hierarchy."""

import pytest

from synthorg.tools.errors import (
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterError,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestToolError:
    """Tests for the base ToolError."""

    def test_message_stored(self) -> None:
        err = ToolError("something broke")
        assert err.message == "something broke"

    def test_context_defaults_to_empty(self) -> None:
        err = ToolError("oops")
        assert err.context == {}

    def test_context_stored(self) -> None:
        ctx = {"tool": "echo", "detail": "missing arg"}
        err = ToolError("oops", context=ctx)
        assert err.context == ctx

    def test_str_without_context(self) -> None:
        err = ToolError("broken")
        assert str(err) == "broken"

    def test_str_with_context(self) -> None:
        err = ToolError("broken", context={"key": "val"})
        assert "broken" in str(err)
        assert "key='val'" in str(err)

    def test_is_exception(self) -> None:
        assert issubclass(ToolError, Exception)


@pytest.mark.unit
class TestErrorHierarchy:
    """Tests for all typed error subclasses."""

    def test_all_subclass_tool_error(self) -> None:
        subclasses = [
            ToolNotFoundError,
            ToolParameterError,
            ToolExecutionError,
        ]
        for cls in subclasses:
            assert issubclass(cls, ToolError)

    def test_catchable_as_tool_error(self) -> None:
        err = ToolNotFoundError("missing")
        with pytest.raises(ToolError):
            raise err

    def test_catchable_as_exception(self) -> None:
        err = ToolParameterError("bad param")
        with pytest.raises(Exception, match="bad param"):
            raise err


@pytest.mark.unit
class TestContextImmutability:
    """Tests for context immutability guarantees."""

    def test_context_is_immutable(self) -> None:
        err = ToolError("oops", context={"key": "val"})
        with pytest.raises(TypeError):
            err.context["new_key"] = "new_val"  # type: ignore[index]

    def test_original_dict_mutation_does_not_affect_error(self) -> None:
        ctx = {"tool": "echo"}
        err = ToolError("oops", context=ctx)
        ctx["extra"] = "injected"
        assert "extra" not in err.context


@pytest.mark.unit
class TestErrorFormatting:
    """Tests for __str__ formatting across error types."""

    def test_all_errors_include_message_in_str(self) -> None:
        for cls in (ToolNotFoundError, ToolParameterError, ToolExecutionError):
            err = cls("test msg", context={"tool": "echo"})
            result = str(err)
            assert "test msg" in result
            assert "tool='echo'" in result

    def test_no_context_just_message(self) -> None:
        err = ToolExecutionError("boom")
        assert str(err) == "boom"
