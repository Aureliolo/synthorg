"""Unit tests for the authored-tool sandbox script handler."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from synthorg.api.state import AppState
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.script_handler import make_dynamic_tool_handler
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import JsonDict

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


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


class _FakeSandbox:
    """Records the last execute call and returns a canned result."""

    def __init__(self, result: SandboxResult) -> None:
        self._result = result
        self.last_call: JsonDict | None = None

    async def execute(  # noqa: PLR0913
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> SandboxResult:
        del cwd, owner_id, project_id
        self.last_call = {
            "command": command,
            "args": args,
            "env_overrides": dict(env_overrides or {}),
            "timeout": timeout,
        }
        return self._result

    async def cleanup(self) -> None:
        return None

    def get_backend_type(self) -> str:
        return "subprocess"

    async def release_owner(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        image_override: str | None = None,
    ) -> None:
        del owner_id, project_id, image_override

    async def health_check(self) -> bool:
        return True


class TestDynamicToolHandler:
    async def test_success_returns_ok_envelope(self) -> None:
        sandbox = _FakeSandbox(
            SandboxResult(stdout='{"slug": "hello-world"}', stderr="", returncode=0)
        )
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        raw = await handler(
            app_state=cast("AppState", None), arguments={"text": "Hello World"}
        )
        envelope = json.loads(raw)
        assert envelope["status"] == "ok"
        assert envelope["data"] == {"slug": "hello-world"}

    async def test_arguments_passed_as_json_env_var(self) -> None:
        sandbox = _FakeSandbox(
            SandboxResult(stdout='{"ok": true}', stderr="", returncode=0)
        )
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        await handler(app_state=cast("AppState", None), arguments={"text": "hi"})
        assert sandbox.last_call is not None
        assert sandbox.last_call["command"] == "python"
        env = sandbox.last_call["env_overrides"]
        assert json.loads(env["SYNTHORG_TOOL_ARGS"]) == {"text": "hi"}

    async def test_nonzero_exit_returns_error_envelope(self) -> None:
        sandbox = _FakeSandbox(SandboxResult(stdout="", stderr="boom", returncode=1))
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        envelope = json.loads(
            await handler(app_state=cast("AppState", None), arguments={"text": "x"})
        )
        assert envelope["status"] == "error"
        assert envelope["domain_code"] == "dynamic_tool_failed"

    async def test_timeout_returns_error_envelope(self) -> None:
        sandbox = _FakeSandbox(
            SandboxResult(stdout="", stderr="", returncode=0, timed_out=True)
        )
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        envelope = json.loads(
            await handler(app_state=cast("AppState", None), arguments={"text": "x"})
        )
        assert envelope["status"] == "error"

    async def test_non_json_stdout_returns_error_envelope(self) -> None:
        sandbox = _FakeSandbox(
            SandboxResult(stdout="not json", stderr="", returncode=0)
        )
        handler = make_dynamic_tool_handler(_blueprint(), sandbox)

        envelope = json.loads(
            await handler(app_state=cast("AppState", None), arguments={"text": "x"})
        )
        assert envelope["status"] == "error"
