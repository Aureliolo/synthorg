"""Tests for sandbox error hierarchy."""

import pytest

from synthorg.tools.errors import ToolError
from synthorg.tools.sandbox.errors import (
    SandboxError,
    SandboxProjectScopeUnresolvedError,
    SandboxShuttingDownError,
    SandboxStartError,
    SandboxSubpathUnsupportedError,
    SandboxTimeoutError,
    SandboxWorkspaceUnmappableError,
    agent_facing_message,
)

pytestmark = pytest.mark.unit


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


#: Every condition an identical later command cannot clear. Handed to an agent
#: as an ordinary tool result each of these reads like a transient failure, and
#: retrying it is what an agent does until its budget runs out: on a recorded
#: sweep six units each burned a 1.5-million-token ceiling doing exactly that.
_TERMINAL: tuple[type[SandboxError], ...] = (
    SandboxShuttingDownError,
    SandboxWorkspaceUnmappableError,
    SandboxSubpathUnsupportedError,
    SandboxProjectScopeUnresolvedError,
)


class TestRetryability:
    """Which failures the agent may act on, and which end the session."""

    def test_a_sandbox_error_is_retryable_by_default(self) -> None:
        """A condition nobody has classified is the agent's to try again."""
        assert SandboxError.RETRYABLE is True

    @pytest.mark.parametrize("error_type", _TERMINAL)
    def test_a_terminal_condition_is_not_retryable(
        self, error_type: type[SandboxError]
    ) -> None:
        """Raised past the tool rather than returned for the agent to retry."""
        assert error_type.RETRYABLE is False

    @pytest.mark.parametrize("error_type", _TERMINAL)
    def test_a_terminal_condition_says_the_command_is_not_the_cause(
        self, error_type: type[SandboxError]
    ) -> None:
        """The two claims are the same one, so they cannot drift apart.

        A message telling the agent its command is blameless while the
        classification invites it to send another is the shape the defect took.
        """
        assert "Nothing about the command caused this" in agent_facing_message(
            error_type("boom")
        )

    @pytest.mark.parametrize("error_type", [SandboxTimeoutError, SandboxStartError])
    def test_a_transient_condition_stays_the_agents_to_retry(
        self, error_type: type[SandboxError]
    ) -> None:
        """A different command, or the same one later, may well succeed."""
        assert error_type.RETRYABLE is True
