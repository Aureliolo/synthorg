"""Planted-defect acceptance test for the headless browser tool.

Canonical acceptance: the org builds a small web app, an agent drives
it headlessly, detects a planted UI / behaviour defect via E2E plus
screenshot-diff, and iterates to green.

This test wires the real ``BrowserTool`` against a real
``DockerSandbox`` running the pinned Playwright image, captures a
golden baseline, plants a defect, and drives a scripted agent loop
through ``AgentEngine``. The agent restores the golden script and
the SSIM-based diff confirms convergence.
"""

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import pytest

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
)
from synthorg.core.enums import Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.hr.seniority import SeniorityLevel
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.tools.browser import BrowserTool
from synthorg.tools.file_system.read_file import ReadFileTool
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from tests._shared.scripted_provider import ScriptedProvider

if TYPE_CHECKING:
    pass

pytestmark = [
    pytest.mark.integration,
    # 300s budget: Docker image pull (~1.5 GB on a cold cache) plus
    # Chromium cold-start plus two full spec rounds plus host-side SSIM
    # diff. The hot-cache path completes in well under a minute.
    pytest.mark.timeout(300),
]

_BROWSER_IMAGE: Final[str] = "mcr.microsoft.com/playwright/python:v1.60.0-jammy"
_MAX_TURNS: Final[int] = 8
_SANDBOX_TIMEOUT_SECONDS: Final[int] = 180
_TEST_MODEL: Final[str] = "test-model-001"
_TEST_PROVIDER: Final[str] = "test-provider"
_FIXTURE_SPEC: Final[str] = "fixture"
_FIXTURE_PATH: Final[str] = "fixture_app/index.html"

_GOLDEN_SCRIPT_JS: Final[str] = (
    "document.querySelector('#submit-btn')"
    ".addEventListener('click', () => {\n"
    "  document.querySelector('#result').textContent = 'Success!';\n"
    "});\n"
)

_DEFECTIVE_SCRIPT_JS: Final[str] = (
    "// Planted defect: click handler intentionally removed.\n"
)

_INDEX_HTML: Final[str] = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Fixture</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 40px;
           background: #ffffff; color: #111111; }
    button#submit-btn { padding: 12px 24px; font-size: 16px;
                        background: #2563eb; color: #ffffff;
                        border: 0; border-radius: 6px; }
    #result { margin-top: 16px; font-size: 18px;
              min-height: 24px; }
  </style>
</head>
<body>
  <button id="submit-btn">Submit</button>
  <div id="result"></div>
  <script src="script.js"></script>
</body>
</html>
"""


def _docker_and_browser_image_available() -> bool:
    """Return True when Docker is reachable and the browser image present."""
    try:
        import aiodocker

        async def _check() -> bool:
            client = None
            try:
                client = aiodocker.Docker()
                await client.version()
                await client.images.inspect(_BROWSER_IMAGE)
            except Exception:
                return False
            else:
                return True
            finally:
                if client is not None:
                    await client.close()

        return asyncio.run(_check())
    except Exception:
        return False


skip_no_browser = pytest.mark.skipif(
    not _docker_and_browser_image_available(),
    reason=(
        "Docker daemon not available or "
        f"{_BROWSER_IMAGE} not pulled (run "
        f"`docker pull {_BROWSER_IMAGE}`)."
    ),
)


def _build_fixture_app(workspace: Path, *, defective: bool) -> Path:
    """Create the fixture app on disk; return the index.html path."""
    app_dir = workspace / "fixture_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    (app_dir / "script.js").write_text(
        _DEFECTIVE_SCRIPT_JS if defective else _GOLDEN_SCRIPT_JS,
        encoding="utf-8",
    )
    return app_dir / "index.html"


def _spec_response(call_id: str, screenshot_name: str) -> CompletionResponse:
    """Cassette entry: agent invokes the browser spec mode."""
    return CompletionResponse(
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=80, output_tokens=20, cost=0.001),
        model=_TEST_MODEL,
        tool_calls=(
            ToolCall(
                id=call_id,
                name="browser",
                arguments={
                    "mode": "spec",
                    "path": _FIXTURE_PATH,
                    "spec_name": _FIXTURE_SPEC,
                    "screenshot_name": screenshot_name,
                    "create_baseline_if_missing": False,
                },
            ),
        ),
    )


def _read_response(call_id: str, path: str) -> CompletionResponse:
    """Cassette entry: agent reads a file."""
    return CompletionResponse(
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=60, output_tokens=10, cost=0.0005),
        model=_TEST_MODEL,
        tool_calls=(
            ToolCall(
                id=call_id,
                name="read_file",
                arguments={"path": path},
            ),
        ),
    )


def _write_response(call_id: str, path: str, content: str) -> CompletionResponse:
    """Cassette entry: agent writes a file."""
    return CompletionResponse(
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=120, output_tokens=20, cost=0.001),
        model=_TEST_MODEL,
        tool_calls=(
            ToolCall(
                id=call_id,
                name="write_file",
                arguments={
                    "path": path,
                    "content": content,
                    "create_directories": False,
                },
            ),
        ),
    )


def _stop_response() -> CompletionResponse:
    """Cassette entry: agent signals completion."""
    return CompletionResponse(
        content="Defect detected via screenshot-diff, restored handler, and verified.",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=30, output_tokens=15, cost=0.0001),
        model=_TEST_MODEL,
    )


def _make_identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Browser Iterating Agent",
        role="QA Engineer",
        department="Engineering",
        level=SeniorityLevel.MID,
        hiring_date=date(2026, 1, 15),
        personality=PersonalityConfig(traits=("methodical",)),
        model=ModelConfig(provider=_TEST_PROVIDER, model_id=_TEST_MODEL),
    )


def _make_task(identity: AgentIdentity) -> Task:
    return Task(
        id="task-1992-acceptance",
        title="Fix the planted UI defect",
        description=(
            "The fixture app's submit button does not display its "
            "success message. Drive it headlessly, detect the "
            "regression, and restore the handler."
        ),
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-1992",
        created_by="manager",
        assigned_to=str(identity.id),
        status=TaskStatus.ASSIGNED,
    )


@skip_no_browser
async def test_browser_iterates_to_green_on_planted_defect(
    tmp_path: Path,
) -> None:
    """Agent detects a planted UI defect and iterates to a passing diff."""
    fixture_html = _build_fixture_app(tmp_path, defective=False)
    assert fixture_html.exists()

    sandbox = DockerSandbox(
        config=DockerSandboxConfig(
            image=_BROWSER_IMAGE,
            timeout_seconds=_SANDBOX_TIMEOUT_SECONDS,
        ),
        workspace=tmp_path,
    )

    try:
        tool = BrowserTool(sandbox=sandbox, workspace=tmp_path)

        baseline_result = await tool.execute(
            arguments={
                "mode": "spec",
                "path": _FIXTURE_PATH,
                "spec_name": _FIXTURE_SPEC,
                "screenshot_name": "baseline",
                "create_baseline_if_missing": True,
            },
        )
        assert not baseline_result.is_error, (
            f"Baseline capture failed: {baseline_result.content!r}"
        )
        baseline_path = tmp_path / ".synthorg" / "screenshots" / _FIXTURE_SPEC
        assert baseline_path.is_dir(), "Baseline directory not created by tool"
        baselines = list(baseline_path.glob("*.png"))
        assert baselines, "Baseline PNG not written to workspace"

        _build_fixture_app(tmp_path, defective=True)
        defective_text = (tmp_path / "fixture_app" / "script.js").read_text(
            encoding="utf-8"
        )
        assert defective_text == _DEFECTIVE_SCRIPT_JS

        provider = ScriptedProvider(
            [
                _spec_response("call-spec-1", "current_001"),
                _read_response("call-read-1", "fixture_app/script.js"),
                _write_response(
                    "call-write-1",
                    "fixture_app/script.js",
                    _GOLDEN_SCRIPT_JS,
                ),
                _spec_response("call-spec-2", "current_002"),
                _stop_response(),
            ],
        )
        identity = _make_identity()
        task = _make_task(identity)
        registry = ToolRegistry(
            [
                tool,
                ReadFileTool(workspace_root=tmp_path),
                WriteFileTool(workspace_root=tmp_path),
            ],
        )
        engine = AgentEngine(provider=provider, tool_registry=registry)

        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=_MAX_TURNS,
        )

        assert result.is_success, (
            f"Agent did not complete cleanly: termination={result.termination_reason}"
        )
        assert result.termination_reason == TerminationReason.COMPLETED

        final_script = (tmp_path / "fixture_app" / "script.js").read_text(
            encoding="utf-8"
        )
        assert final_script == _GOLDEN_SCRIPT_JS, (
            "Agent did not restore the golden script.js content"
        )

        conversation = result.execution_result.context.conversation
        spec_payloads: list[dict[str, object]] = []
        for message in conversation:
            if message.tool_result is None:
                continue
            try:
                decoded = json.loads(message.tool_result.content)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict) and "diff" in decoded:
                spec_payloads.append(decoded)
        assert len(spec_payloads) >= 2, (
            "Expected at least two browser spec results (detect plus verify)"
        )
        first_diff = spec_payloads[0]["diff"]
        last_diff = spec_payloads[-1]["diff"]
        assert isinstance(first_diff, dict)
        assert isinstance(last_diff, dict)
        assert first_diff["passed_tolerance"] is False, (
            f"First spec must show diff failure; diff={first_diff!r}"
        )
        assert last_diff["passed_tolerance"] is True, (
            f"Final spec must show diff passing; diff={last_diff!r}"
        )
    finally:
        await sandbox.cleanup()
