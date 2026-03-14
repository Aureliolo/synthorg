"""Tests for sandbox error hierarchy."""

import pytest

from synthorg.tools.errors import ToolError
from synthorg.tools.sandbox.errors import (
    SandboxError,
    SandboxStartError,
    SandboxTimeoutError,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestSandboxErrors:
    """Sandbox errors inherit from ToolError and carry context."""

    def test_sandbox_error_inherits_tool_error(self) -> None:
        err = SandboxError("boom")
        assert isinstance(err, ToolError)

    def test_sandbox_timeout_inherits_sandbox_error(self) -> None:
        err = SandboxTimeoutError("timed out")
        assert isinstance(err, SandboxError)
        assert isinstance(err, ToolError)

    def test_sandbox_start_inherits_sandbox_error(self) -> None:
        err = SandboxStartError("start failed")
        assert isinstance(err, SandboxError)
        assert isinstance(err, ToolError)

    def test_error_message(self) -> None:
        err = SandboxError("test message")
        assert err.message == "test message"
        assert str(err) == "test message"

    def test_error_with_context(self) -> None:
        err = SandboxStartError(
            "failed",
            context={"command": "git"},
        )
        assert err.context["command"] == "git"
        assert "git" in str(err)

    def test_error_context_is_immutable(self) -> None:
        err = SandboxError("boom", context={"key": "value"})
        with pytest.raises(TypeError):
            err.context["new_key"] = "nope"  # type: ignore[index]
