"""Unit tests for desktop result models, errors, and permission gating."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ToolCategory
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.tools.desktop._models import (
    InputResult,
    LaunchResult,
    ScreenshotResult,
)
from synthorg.tools.desktop.errors import (
    DesktopAppNotRunningError,
    DesktopArgumentError,
    DesktopDomainError,
    DesktopScreenshotError,
)
from synthorg.tools.errors import ToolError
from synthorg.tools.permissions import ToolPermissionChecker

pytestmark = pytest.mark.unit


class TestModels:
    def test_launch_result_frozen(self) -> None:
        result = LaunchResult(
            display=":99",
            pid=10,
            screen_width=800,
            screen_height=600,
        )
        with pytest.raises(ValidationError):
            result.pid = 11

    def test_input_result_default_detail(self) -> None:
        assert InputResult(action="click").detail == ""

    def test_screenshot_result_rejects_bad_sha(self) -> None:
        with pytest.raises(ValidationError):
            ScreenshotResult(
                saved_path="a.png",
                width=1,
                height=1,
                file_size_bytes=0,
                captured_at_iso="2026-05-21T00:00:00+00:00",
                sha256="not-a-sha",
            )


class TestErrors:
    def test_hierarchy(self) -> None:
        assert issubclass(DesktopDomainError, ToolError)
        assert issubclass(DesktopScreenshotError, DesktopDomainError)

    def test_argument_error_is_422(self) -> None:
        assert DesktopArgumentError.status_code == 422

    def test_app_not_running_is_409(self) -> None:
        assert DesktopAppNotRunningError.status_code == 409


class TestPermissions:
    def test_desktop_permitted_at_standard(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.STANDARD)
        assert checker.is_permitted("desktop", ToolCategory.DESKTOP)

    def test_desktop_denied_at_sandboxed(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.SANDBOXED)
        assert not checker.is_permitted("desktop", ToolCategory.DESKTOP)

    def test_desktop_permitted_at_elevated(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.ELEVATED)
        assert checker.is_permitted("desktop", ToolCategory.DESKTOP)
