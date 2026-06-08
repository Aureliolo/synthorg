"""Unit tests for sub-constraint models and level defaults."""

import pytest

from synthorg.core.tool_constraints import (
    _LEVEL_SUB_CONSTRAINTS,
    CodeExecutionIsolation,
    FileSystemScope,
    GitAccess,
    NetworkMode,
    TerminalAccess,
    ToolAccessLevel,
    ToolSubConstraints,
    get_sub_constraints,
)

# ── Enum values ────────────────────────────────────────────────


class TestEnumValues:
    """Verify enum members match the design spec."""

    @pytest.mark.unit
    def test_file_system_scope_members(self) -> None:
        assert set(FileSystemScope) == {
            FileSystemScope.WORKSPACE_ONLY,
            FileSystemScope.PROJECT_DIRECTORY,
            FileSystemScope.FULL,
        }

    @pytest.mark.unit
    def test_network_mode_members(self) -> None:
        assert set(NetworkMode) == {
            NetworkMode.NONE,
            NetworkMode.ALLOWLIST_ONLY,
            NetworkMode.OPEN,
        }

    @pytest.mark.unit
    def test_git_access_members(self) -> None:
        assert set(GitAccess) == {
            GitAccess.LOCAL_ONLY,
            GitAccess.READ_AND_BRANCH,
            GitAccess.FULL,
        }

    @pytest.mark.unit
    def test_code_execution_isolation_members(self) -> None:
        assert set(CodeExecutionIsolation) == {
            CodeExecutionIsolation.CONTAINERIZED,
            CodeExecutionIsolation.PROCESS,
        }

    @pytest.mark.unit
    def test_terminal_access_members(self) -> None:
        assert set(TerminalAccess) == {
            TerminalAccess.NONE,
            TerminalAccess.RESTRICTED_COMMANDS,
            TerminalAccess.FULL,
        }


# ── ToolSubConstraints model ──────────────────────────────────


class TestToolSubConstraints:
    """Tests for the frozen Pydantic model."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        sc = ToolSubConstraints()
        assert sc.file_system == FileSystemScope.PROJECT_DIRECTORY
        assert sc.network == NetworkMode.OPEN
        assert sc.git == GitAccess.FULL
        assert sc.code_execution == CodeExecutionIsolation.CONTAINERIZED
        assert sc.terminal == TerminalAccess.RESTRICTED_COMMANDS
        assert sc.requires_approval == ()
        assert sc.network_allowlist == ()

    @pytest.mark.unit
    def test_frozen(self) -> None:
        sc = ToolSubConstraints()
        with pytest.raises(Exception):  # noqa: B017, PT011
            sc.network = NetworkMode.NONE  # type: ignore[misc]

    @pytest.mark.unit
    def test_custom_values(self) -> None:
        sc = ToolSubConstraints(
            file_system=FileSystemScope.WORKSPACE_ONLY,
            network=NetworkMode.NONE,
            git=GitAccess.LOCAL_ONLY,
            terminal=TerminalAccess.NONE,
            requires_approval=("deployment",),
            network_allowlist=("db.internal:5432",),
        )
        assert sc.file_system == FileSystemScope.WORKSPACE_ONLY
        assert sc.network == NetworkMode.NONE
        assert sc.requires_approval == ("deployment",)
        assert sc.network_allowlist == ("db.internal:5432",)


# ── Level defaults ─────────────────────────────────────────────


class TestLevelSubConstraints:
    """Tests for the per-level default mapping."""

    @pytest.mark.unit
    def test_all_built_in_levels_have_defaults(self) -> None:
        for level in ToolAccessLevel:
            if level == ToolAccessLevel.CUSTOM:
                assert level not in _LEVEL_SUB_CONSTRAINTS
            else:
                assert level in _LEVEL_SUB_CONSTRAINTS

    @pytest.mark.unit
    def test_sandboxed_is_most_restrictive(self) -> None:
        sc = _LEVEL_SUB_CONSTRAINTS[ToolAccessLevel.SANDBOXED]
        assert sc.file_system == FileSystemScope.WORKSPACE_ONLY
        assert sc.network == NetworkMode.NONE
        assert sc.git == GitAccess.LOCAL_ONLY
        assert sc.terminal == TerminalAccess.NONE

    @pytest.mark.unit
    def test_restricted_has_allowlist_network(self) -> None:
        sc = _LEVEL_SUB_CONSTRAINTS[ToolAccessLevel.RESTRICTED]
        assert sc.network == NetworkMode.ALLOWLIST_ONLY
        assert sc.git == GitAccess.READ_AND_BRANCH
        assert sc.terminal == TerminalAccess.NONE
        assert "deploy:" in sc.requires_approval

    @pytest.mark.unit
    def test_standard_has_open_network(self) -> None:
        sc = _LEVEL_SUB_CONSTRAINTS[ToolAccessLevel.STANDARD]
        assert sc.network == NetworkMode.OPEN
        assert sc.git == GitAccess.FULL
        assert sc.terminal == TerminalAccess.RESTRICTED_COMMANDS

    @pytest.mark.unit
    def test_elevated_has_full_access(self) -> None:
        sc = _LEVEL_SUB_CONSTRAINTS[ToolAccessLevel.ELEVATED]
        assert sc.file_system == FileSystemScope.FULL
        assert sc.terminal == TerminalAccess.FULL

    @pytest.mark.unit
    def test_all_levels_containerized(self) -> None:
        """Design spec: all levels use containerized code execution."""
        for sc in _LEVEL_SUB_CONSTRAINTS.values():
            assert sc.code_execution == CodeExecutionIsolation.CONTAINERIZED


# ── get_sub_constraints ────────────────────────────────────────


class TestGetSubConstraints:
    """Tests for the resolution function."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "level",
        [
            ToolAccessLevel.SANDBOXED,
            ToolAccessLevel.RESTRICTED,
            ToolAccessLevel.STANDARD,
            ToolAccessLevel.ELEVATED,
        ],
    )
    def test_built_in_levels_return_defaults(self, level: ToolAccessLevel) -> None:
        result = get_sub_constraints(level)
        assert result == _LEVEL_SUB_CONSTRAINTS[level]

    @pytest.mark.unit
    def test_custom_level_without_constraints_raises(self) -> None:
        with pytest.raises(ValueError, match=r"CUSTOM.*requires"):
            get_sub_constraints(ToolAccessLevel.CUSTOM)

    @pytest.mark.unit
    def test_custom_level_with_constraints_returns_them(self) -> None:
        custom = ToolSubConstraints(
            network=NetworkMode.NONE,
            terminal=TerminalAccess.NONE,
        )
        result = get_sub_constraints(ToolAccessLevel.CUSTOM, custom)
        assert result is custom

    @pytest.mark.unit
    def test_custom_overrides_built_in_level(self) -> None:
        custom = ToolSubConstraints(network=NetworkMode.NONE)
        result = get_sub_constraints(ToolAccessLevel.STANDARD, custom)
        # Overridden field uses the custom value.
        assert result.network == NetworkMode.NONE
        # Non-overridden fields retain STANDARD level defaults.
        assert result.terminal == TerminalAccess.RESTRICTED_COMMANDS
