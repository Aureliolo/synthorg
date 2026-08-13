"""Unit tests for the authored-tool sandbox script handler."""

import json
from datetime import UTC, datetime

import pytest

from synthorg.api.state import AppState
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.script_handler import (
    make_dynamic_tool_handler,
    run_dynamic_tool_probe,
)
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import FakeSandbox, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
_BACKEND_UNAVAILABLE = "backend unavailable"


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id="bp-1",
        name="synthorg_textkit_slugify",
        description="Slugify text.",
        capability="textkit:slugify",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        script_body="import os, json; print(json.dumps({'ok': True}))",
        action_type="code:read",
        created_at=_NOW,
    )


def _raising_sandbox() -> FakeSandbox:
    """A sandbox whose execute raises a non-``ToolsmithError`` transport fault.

    Returns:
        The double.
    """
    return FakeSandbox(error=RuntimeError(_BACKEND_UNAVAILABLE))


class TestDynamicToolHandler:
    async def test_success_returns_ok_envelope(self) -> None:
        sandbox = FakeSandbox(
            SandboxResult(stdout='{"slug": "hello-world"}', stderr="", returncode=0)
        )
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        raw = await handler(
            app_state=mock_of[AppState](), arguments={"text": "Hello World"}
        )
        envelope = json.loads(raw)
        assert envelope["status"] == "ok"
        assert envelope["data"] == {"slug": "hello-world"}

    async def test_arguments_passed_as_json_env_var(self) -> None:
        sandbox = FakeSandbox(
            SandboxResult(stdout='{"ok": true}', stderr="", returncode=0)
        )
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        await handler(app_state=mock_of[AppState](), arguments={"text": "hi"})
        assert sandbox.last_call is not None
        assert sandbox.last_call.command == "python"
        env = sandbox.last_call.env_overrides
        assert json.loads(env["SYNTHORG_TOOL_ARGS"]) == {"text": "hi"}

    async def test_nonzero_exit_returns_error_envelope(self) -> None:
        sandbox = FakeSandbox(SandboxResult(stdout="", stderr="boom", returncode=1))
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        envelope = json.loads(
            await handler(app_state=mock_of[AppState](), arguments={"text": "x"})
        )
        assert envelope["status"] == "error"
        assert envelope["domain_code"] == "dynamic_tool_failed"

    async def test_timeout_returns_error_envelope(self) -> None:
        sandbox = FakeSandbox(
            SandboxResult(stdout="", stderr="", returncode=0, timed_out=True)
        )
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        envelope = json.loads(
            await handler(app_state=mock_of[AppState](), arguments={"text": "x"})
        )
        assert envelope["status"] == "error"
        assert envelope["domain_code"] == "dynamic_tool_failed"

    async def test_non_json_stdout_returns_error_envelope(self) -> None:
        sandbox = FakeSandbox(SandboxResult(stdout="not json", stderr="", returncode=0))
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        envelope = json.loads(
            await handler(app_state=mock_of[AppState](), arguments={"text": "x"})
        )
        assert envelope["status"] == "error"

    async def test_unexpected_sandbox_exception_is_wrapped(self) -> None:
        """A non-ToolsmithError from the sandbox is wrapped, not leaked.

        ``_execute_script`` only raises ``DynamicToolScriptError`` for the
        expected failure modes; a transport fault (e.g. the backend raising
        ``RuntimeError``) takes the ``isinstance(exc, ToolsmithError)`` False
        branch in ``_script_to_envelope`` and must still produce the standard
        error envelope rather than propagating.
        """
        handler = make_dynamic_tool_handler(_blueprint(), _raising_sandbox())

        envelope = json.loads(
            await handler(app_state=mock_of[AppState](), arguments={"text": "x"})
        )
        assert envelope["status"] == "error"
        assert envelope["domain_code"] == "dynamic_tool_failed"


class TestRunDynamicToolProbe:
    """The app-state-free probe entry point used by the validation gate."""

    async def test_probe_success_returns_ok_envelope(self) -> None:
        sandbox = FakeSandbox(
            SandboxResult(stdout='{"slug": "x"}', stderr="", returncode=0)
        )
        envelope = json.loads(
            await run_dynamic_tool_probe(_blueprint(), sandbox, {"text": "x"})
        )
        assert envelope["status"] == "ok"
        assert envelope["data"] == {"slug": "x"}

    async def test_probe_wraps_unexpected_sandbox_exception(self) -> None:
        envelope = json.loads(
            await run_dynamic_tool_probe(
                _blueprint(), _raising_sandbox(), {"text": "x"}
            )
        )
        assert envelope["status"] == "error"
        assert envelope["domain_code"] == "dynamic_tool_failed"
