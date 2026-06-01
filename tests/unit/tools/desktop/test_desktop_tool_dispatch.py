"""Unit tests for DesktopTool dispatch + sandbox interaction.

These tests fake the sandbox boundary via ``mock_of[SandboxBackend]``
so the host-side flow can be exercised without booting Xvfb or Docker.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import ToolCategory
from synthorg.tools.desktop import DesktopTool
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared.fake_clock import FakeClock
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit


def _sandbox_result(payload: dict[str, Any]) -> SandboxResult:
    return SandboxResult(
        stdout=json.dumps(payload),
        stderr="",
        returncode=0,
        timed_out=False,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _fake_sandbox(result: SandboxResult) -> Any:
    return mock_of[SandboxBackend](
        execute=AsyncMock(spec=SandboxBackend.execute, return_value=result),
        release_owner=AsyncMock(spec=SandboxBackend.release_owner),
    )


def _tool(workspace: Path, payload: dict[str, Any]) -> DesktopTool:
    return DesktopTool(
        sandbox=_fake_sandbox(_sandbox_result(payload)),
        workspace=workspace,
    )


class TestDesktopToolBasics:
    def test_category_and_name(self, workspace: Path) -> None:
        tool = _tool(workspace, {})
        assert tool.category is ToolCategory.DESKTOP
        assert tool.name == "desktop"

    def test_requires_absolute_workspace(self) -> None:
        from synthorg.tools.desktop.errors import DesktopDomainError

        with pytest.raises(DesktopDomainError):
            DesktopTool(
                sandbox=_fake_sandbox(_sandbox_result({})),
                workspace=Path("relative"),
            )


class TestDesktopToolDispatch:
    async def test_launch_returns_launch_result(self, workspace: Path) -> None:
        payload = {
            "status": "ok",
            "result": {
                "display": ":99",
                "pid": 4321,
                "screen_width": 1280,
                "screen_height": 800,
            },
        }
        tool = _tool(workspace, payload)
        result = await tool.execute(
            arguments={"mode": "launch", "app_command": "python3 /workspace/app.py"},
        )
        assert result.is_error is False
        assert result.metadata["pid"] == 4321
        assert result.metadata["display"] == ":99"

    async def test_click_returns_input_result(self, workspace: Path) -> None:
        payload = {"status": "ok", "result": {"action": "click", "detail": "button 1"}}
        tool = _tool(workspace, payload)
        result = await tool.execute(arguments={"mode": "click", "x": 5, "y": 6})
        assert result.is_error is False
        assert result.metadata["action"] == "click"

    async def test_screenshot_returns_metadata(self, workspace: Path) -> None:
        payload = {
            "status": "ok",
            "result": {
                "saved_path": "/workspace/.synthorg/desktop/screenshots/shot.png",
                "width": 1280,
                "height": 800,
                "file_size_bytes": 2048,
                "sha256": "a" * 64,
            },
        }
        tool = _tool(workspace, payload)
        result = await tool.execute(
            arguments={"mode": "screenshot", "screenshot_name": "shot"},
        )
        assert result.is_error is False
        meta = cast("dict[str, Any]", result.metadata)
        assert meta["sha256"] == "a" * 64
        assert meta["saved_path"].endswith("shot.png")

    async def test_screenshot_stamps_injected_clock(self, workspace: Path) -> None:
        fixed = datetime(2026, 5, 21, 7, 0, tzinfo=UTC)
        payload = {
            "status": "ok",
            "result": {
                "saved_path": "/workspace/.synthorg/desktop/screenshots/shot.png",
                "width": 640,
                "height": 480,
                "file_size_bytes": 1024,
                "sha256": "b" * 64,
            },
        }
        tool = DesktopTool(
            sandbox=_fake_sandbox(_sandbox_result(payload)),
            workspace=workspace,
            clock=FakeClock(start=fixed),
        )
        result = await tool.execute(
            arguments={"mode": "screenshot", "screenshot_name": "shot"},
        )
        assert result.metadata["captured_at_iso"] == fixed.isoformat()

    async def test_malformed_executor_result_maps_to_error(
        self, workspace: Path
    ) -> None:
        # A success envelope whose result omits required fields is
        # protocol drift, not a successful launch.
        payload = {"status": "ok", "result": {"display": ":99"}}
        tool = _tool(workspace, payload)
        result = await tool.execute(
            arguments={"mode": "launch", "app_command": "python3 /workspace/app.py"},
        )
        assert result.is_error is True
        assert result.metadata["error_type"] == "DesktopSessionError"

    async def test_app_not_running_maps_to_error(self, workspace: Path) -> None:
        payload = {
            "status": "error",
            "error_type": "DesktopAppNotRunningError",
            "message": "No GUI application is running",
        }
        tool = _tool(workspace, payload)
        result = await tool.execute(arguments={"mode": "click", "x": 1, "y": 1})
        assert result.is_error is True
        assert result.metadata["error_type"] == "DesktopAppNotRunningError"

    async def test_invalid_arguments_error(self, workspace: Path) -> None:
        tool = _tool(workspace, {})
        # launch without app_command violates the per-mode invariant.
        result = await tool.execute(arguments={"mode": "launch"})
        assert result.is_error is True
        assert result.metadata["error_type"] == "DesktopArgumentError"

    async def test_timeout_maps_to_session_error(self, workspace: Path) -> None:
        timed_out = SandboxResult(stdout="", stderr="", returncode=0, timed_out=True)
        tool = DesktopTool(sandbox=_fake_sandbox(timed_out), workspace=workspace)
        result = await tool.execute(
            arguments={"mode": "screenshot", "screenshot_name": "x"},
        )
        assert result.is_error is True
        assert result.metadata["error_type"] == "DesktopSessionError"

    async def test_cleanup_releases_owner(self, workspace: Path) -> None:
        sandbox = _fake_sandbox(_sandbox_result({}))
        tool = DesktopTool(sandbox=sandbox, workspace=workspace)
        await tool.cleanup()
        sandbox.release_owner.assert_awaited_once()
