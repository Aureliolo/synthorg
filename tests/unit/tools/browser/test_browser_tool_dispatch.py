"""Unit tests for BrowserTool dispatch + sandbox interaction.

These tests fake the sandbox boundary via ``mock_of[SandboxBackend]``
and a stubbed differ so the host-side flow can be exercised without
booting Playwright or Docker.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import ToolCategory
from synthorg.tools.browser._models import ScreenshotDiffResult
from synthorg.tools.browser._protocols import ScreenshotDiffer
from synthorg.tools.browser.browser_tool import BrowserTool
from synthorg.tools.browser.errors import (
    BrowserBaselineNotFoundError,
)
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit


_VIEWPORT = (800, 600)


def _executor_payload(*, screenshot_path: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "navigation": {
            "requested_url": "file:///workspace/fixture/index.html",
            "final_url": "file:///workspace/fixture/index.html",
            "status_code": 200,
            "duration_seconds": 0.1,
        },
        "screenshot": {
            "saved_path": screenshot_path,
            "width": _VIEWPORT[0],
            "height": _VIEWPORT[1],
            "file_size_bytes": 4096,
            "full_page": False,
            "sha256": "b" * 64,
        },
        "accessibility": {
            "url": "file:///workspace/fixture/index.html",
            "min_impact": "serious",
            "violations": [],
            "warnings": [],
            "total_affected_nodes": 0,
            "scan_duration_seconds": 0.05,
            "axe_version": "4.10.2",
            "passed": True,
        },
    }


def _sandbox_success(payload: dict[str, Any]) -> SandboxResult:
    return SandboxResult(
        stdout=json.dumps(payload),
        stderr="",
        returncode=0,
        timed_out=False,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def fake_sandbox() -> Any:
    return mock_of[SandboxBackend](
        execute=AsyncMock(spec=SandboxBackend.execute),
        release_owner=AsyncMock(spec=SandboxBackend.release_owner),
    )


class _NoOpDiffer:
    """ScreenshotDiffer double returning a fixed score."""

    def __init__(self, score: float) -> None:
        self._score = score

    async def compare(
        self,
        *,
        baseline: Path,
        current: Path,
        tolerance: float,
        diff_output: Path,
    ) -> float:
        del baseline, current, tolerance
        diff_output.parent.mkdir(parents=True, exist_ok=True)
        diff_output.write_bytes(b"diff")  # noqa: ASYNC240 -- test-only stub
        return self._score


class TestBrowserToolBasics:
    def test_category_is_browser(
        self,
        workspace: Path,
        fake_sandbox: Any,
    ) -> None:
        tool = BrowserTool(sandbox=fake_sandbox, workspace=workspace)
        assert tool.category is ToolCategory.BROWSER
        assert tool.name == "browser"

    def test_assert_screenshot_differ_is_pluggable(
        self,
        workspace: Path,
        fake_sandbox: Any,
    ) -> None:
        differ = _NoOpDiffer(0.99)
        tool = BrowserTool(
            sandbox=fake_sandbox,
            workspace=workspace,
            screenshot_differ=differ,
        )
        # Ensure it satisfies the protocol structurally.
        assert isinstance(differ, ScreenshotDiffer)
        assert tool.category is ToolCategory.BROWSER


class TestBrowserToolDispatch:
    async def test_invalid_mode_returns_argument_error(
        self,
        workspace: Path,
        fake_sandbox: Any,
    ) -> None:
        tool = BrowserTool(sandbox=fake_sandbox, workspace=workspace)
        result = await tool.execute(
            arguments={"mode": "spec"},  # missing required fields
        )
        assert result.is_error is True
        assert result.metadata["error_type"] == "BrowserArgumentError"

    async def test_spec_baseline_missing_raises(
        self,
        workspace: Path,
        fake_sandbox: Any,
    ) -> None:
        # Sandbox returns a successful executor payload.
        fake_sandbox.execute.return_value = _sandbox_success(
            _executor_payload(
                screenshot_path=str(
                    workspace
                    / ".synthorg"
                    / "screenshots"
                    / "spec1"
                    / "hero.current.png",
                ),
            ),
        )

        tool = BrowserTool(
            sandbox=fake_sandbox,
            workspace=workspace,
            screenshot_differ=_NoOpDiffer(0.999),
        )

        # Simulate the executor having written the file the host expects.
        current_path = (
            workspace / ".synthorg" / "screenshots" / "spec1" / "hero.current.png"
        )
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_bytes(b"png")

        result = await tool.execute(
            arguments={
                "mode": "spec",
                "path": "fixture/index.html",
                "spec_name": "spec1",
                "screenshot_name": "hero",
            },
        )
        assert result.is_error is True
        assert result.metadata["error_type"] == BrowserBaselineNotFoundError.__name__

    async def test_spec_creates_baseline_when_flag_set(
        self,
        workspace: Path,
        fake_sandbox: Any,
    ) -> None:
        fake_sandbox.execute.return_value = _sandbox_success(
            _executor_payload(
                screenshot_path=str(
                    workspace
                    / ".synthorg"
                    / "screenshots"
                    / "spec1"
                    / "hero.current.png",
                ),
            ),
        )

        tool = BrowserTool(
            sandbox=fake_sandbox,
            workspace=workspace,
            screenshot_differ=_NoOpDiffer(0.999),
        )
        current_path = (
            workspace / ".synthorg" / "screenshots" / "spec1" / "hero.current.png"
        )
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_bytes(b"png")

        result = await tool.execute(
            arguments={
                "mode": "spec",
                "path": "fixture/index.html",
                "spec_name": "spec1",
                "screenshot_name": "hero",
                "create_baseline_if_missing": True,
            },
        )
        assert result.is_error is False, result.content
        diff = ScreenshotDiffResult.model_validate(
            result.metadata["diff"],
        )
        assert diff.is_baseline_new is True
        assert diff.passed_tolerance is True
        baseline_path = workspace / ".synthorg" / "screenshots" / "spec1" / "hero.png"
        assert baseline_path.exists()
