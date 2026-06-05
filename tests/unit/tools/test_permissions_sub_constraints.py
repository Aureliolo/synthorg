"""Unit tests for ToolPermissionChecker sub-constraint integration."""

import pytest

from synthorg.core.enums import ActionType, ToolAccessLevel, ToolCategory
from synthorg.core.tool_constraints import (
    NetworkMode,
    TerminalAccess,
    ToolSubConstraints,
)
from synthorg.tools.permissions import ToolPermissionChecker


class TestCheckerSubConstraints:
    """Tests for sub-constraint integration in ToolPermissionChecker."""

    @pytest.mark.unit
    def test_default_sub_constraints_from_level(self) -> None:
        """Built-in levels auto-resolve sub-constraints."""
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.SANDBOXED)
        assert checker._sub_enforcer is not None
        violation = checker.check_sub_constraints(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        # Sandboxed has network=NONE, so web tools should be denied
        assert violation is not None
        assert violation.constraint == "network"

    @pytest.mark.unit
    def test_custom_sub_constraints_override(self) -> None:
        custom = ToolSubConstraints(network=NetworkMode.NONE)
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.STANDARD,
            sub_constraints=custom,
        )
        violation = checker.check_sub_constraints(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert violation is not None

    @pytest.mark.unit
    def test_custom_level_without_constraints_no_enforcement(self) -> None:
        """Bare CUSTOM level has no sub-constraint enforcer."""
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.CUSTOM,
            allowed=frozenset({"http_request"}),
        )
        assert checker._sub_enforcer is None
        violation = checker.check_sub_constraints(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert violation is None

    @pytest.mark.unit
    def test_custom_level_with_constraints(self) -> None:
        custom = ToolSubConstraints(
            network=NetworkMode.OPEN,
            terminal=TerminalAccess.NONE,
        )
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.CUSTOM,
            allowed=frozenset({"shell_command"}),
            sub_constraints=custom,
        )
        violation = checker.check_sub_constraints(
            "shell_command", ToolCategory.TERMINAL, ActionType.CODE_WRITE, {}
        )
        assert violation is not None
        assert violation.constraint == "terminal"

    @pytest.mark.unit
    def test_standard_level_allows_web(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.STANDARD)
        violation = checker.check_sub_constraints(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert violation is None

    @pytest.mark.unit
    def test_sandboxed_blocks_terminal(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.SANDBOXED)
        violation = checker.check_sub_constraints(
            "shell_command", ToolCategory.TERMINAL, ActionType.CODE_WRITE, {}
        )
        assert violation is not None
        assert violation.constraint == "terminal"

    @pytest.mark.unit
    def test_restricted_blocks_git_push(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.RESTRICTED)
        violation = checker.check_sub_constraints(
            "git_push",
            ToolCategory.VERSION_CONTROL,
            ActionType.VCS_PUSH,
            {},
        )
        assert violation is not None
        assert "push" in violation.reason

    @pytest.mark.unit
    def test_elevated_allows_everything(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.ELEVATED)
        for tool_name, category, action_type in [
            ("http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL),
            ("shell_command", ToolCategory.TERMINAL, ActionType.CODE_WRITE),
            ("git_push", ToolCategory.VERSION_CONTROL, ActionType.VCS_PUSH),
        ]:
            violation = checker.check_sub_constraints(
                tool_name, category, action_type, {}
            )
            assert violation is None, f"{tool_name} should be allowed"


class TestFromPermissions:
    """Tests for from_permissions with sub_constraints."""

    @pytest.mark.unit
    def test_passes_sub_constraints_through(self) -> None:
        from synthorg.core.agent import ToolPermissions

        custom = ToolSubConstraints(network=NetworkMode.NONE)
        perms = ToolPermissions(
            access_level=ToolAccessLevel.STANDARD,
            sub_constraints=custom,
        )
        checker = ToolPermissionChecker.from_permissions(perms)
        violation = checker.check_sub_constraints(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert violation is not None

    @pytest.mark.unit
    def test_none_sub_constraints_uses_defaults(self) -> None:
        from synthorg.core.agent import ToolPermissions

        perms = ToolPermissions(access_level=ToolAccessLevel.SANDBOXED)
        checker = ToolPermissionChecker.from_permissions(perms)
        violation = checker.check_sub_constraints(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert violation is not None
