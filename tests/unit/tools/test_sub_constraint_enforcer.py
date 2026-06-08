"""Unit tests for the sub-constraint enforcer."""

import pytest
from pydantic import ValidationError

from synthorg.core.tool_constraints import (
    FileSystemScope,
    GitAccess,
    NetworkMode,
    TerminalAccess,
    ToolSubConstraints,
)
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools.sub_constraint_enforcer import (
    SubConstraintEnforcer,
    SubConstraintViolation,
)

# ── SubConstraintViolation model ───────────────────────────────


class TestSubConstraintViolation:
    """Tests for the violation model."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        v = SubConstraintViolation(constraint="network", reason="blocked")
        assert v.constraint == "network"
        assert v.reason == "blocked"
        assert v.requires_approval is False

    @pytest.mark.unit
    def test_requires_approval(self) -> None:
        v = SubConstraintViolation(
            constraint="requires_approval",
            reason="needs approval",
            requires_approval=True,
        )
        assert v.requires_approval is True

    @pytest.mark.unit
    def test_frozen(self) -> None:
        v = SubConstraintViolation(constraint="test", reason="test")
        with pytest.raises(ValidationError):
            v.constraint = "other"  # type: ignore[misc]


# ── Network constraint ─────────────────────────────────────────


class TestNetworkConstraint:
    """Tests for network mode enforcement."""

    @pytest.mark.unit
    def test_none_blocks_web_tools(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(network=NetworkMode.NONE),
        )
        result = enforcer.check(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert result is not None
        assert result.constraint == "network"

    @pytest.mark.unit
    def test_none_allows_non_network_tools(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(network=NetworkMode.NONE),
        )
        result = enforcer.check(
            "read_file", ToolCategory.FILE_SYSTEM, ActionType.CODE_READ, {}
        )
        assert result is None

    @pytest.mark.unit
    def test_open_allows_web_tools(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(network=NetworkMode.OPEN),
        )
        result = enforcer.check(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert result is None

    @pytest.mark.unit
    def test_allowlist_allows_web_tools(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(network=NetworkMode.ALLOWLIST_ONLY),
        )
        result = enforcer.check(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert result is None


# ── Terminal constraint ────────────────────────────────────────


class TestTerminalConstraint:
    """Tests for terminal access enforcement."""

    @pytest.mark.unit
    def test_none_blocks_terminal_tools(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(terminal=TerminalAccess.NONE),
        )
        result = enforcer.check(
            "shell_command", ToolCategory.TERMINAL, ActionType.CODE_WRITE, {}
        )
        assert result is not None
        assert result.constraint == "terminal"

    @pytest.mark.unit
    def test_restricted_allows_terminal_tools(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(terminal=TerminalAccess.RESTRICTED_COMMANDS),
        )
        result = enforcer.check(
            "shell_command", ToolCategory.TERMINAL, ActionType.CODE_WRITE, {}
        )
        assert result is None

    @pytest.mark.unit
    def test_full_allows_terminal_tools(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(terminal=TerminalAccess.FULL),
        )
        result = enforcer.check(
            "shell_command", ToolCategory.TERMINAL, ActionType.CODE_WRITE, {}
        )
        assert result is None

    @pytest.mark.unit
    def test_none_allows_non_terminal_tools(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(terminal=TerminalAccess.NONE),
        )
        result = enforcer.check(
            "read_file", ToolCategory.FILE_SYSTEM, ActionType.CODE_READ, {}
        )
        assert result is None


# ── Git constraint ─────────────────────────────────────────────


class TestGitConstraint:
    """Tests for git access enforcement."""

    @pytest.mark.unit
    def test_local_only_blocks_push(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(git=GitAccess.LOCAL_ONLY),
        )
        result = enforcer.check(
            "git_push", ToolCategory.VERSION_CONTROL, ActionType.VCS_PUSH, {}
        )
        assert result is not None
        assert result.constraint == "git"
        assert "push" in result.reason

    @pytest.mark.unit
    def test_local_only_blocks_clone(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(git=GitAccess.LOCAL_ONLY),
        )
        result = enforcer.check(
            "git_clone",
            ToolCategory.VERSION_CONTROL,
            ActionType.VCS_READ,
            {},
        )
        assert result is not None
        assert result.constraint == "git"
        assert "clone" in result.reason

    @pytest.mark.unit
    def test_local_only_allows_status(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(git=GitAccess.LOCAL_ONLY),
        )
        result = enforcer.check(
            "git_status",
            ToolCategory.VERSION_CONTROL,
            ActionType.VCS_READ,
            {},
        )
        assert result is None

    @pytest.mark.unit
    def test_read_and_branch_blocks_push(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(git=GitAccess.READ_AND_BRANCH),
        )
        result = enforcer.check(
            "git_push", ToolCategory.VERSION_CONTROL, ActionType.VCS_PUSH, {}
        )
        assert result is not None
        assert "push" in result.reason

    @pytest.mark.unit
    def test_read_and_branch_allows_clone(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(git=GitAccess.READ_AND_BRANCH),
        )
        result = enforcer.check(
            "git_clone",
            ToolCategory.VERSION_CONTROL,
            ActionType.VCS_READ,
            {},
        )
        assert result is None

    @pytest.mark.unit
    def test_full_allows_everything(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(git=GitAccess.FULL),
        )
        result = enforcer.check(
            "git_push", ToolCategory.VERSION_CONTROL, ActionType.VCS_PUSH, {}
        )
        assert result is None


# ── Requires approval ──────────────────────────────────────────


class TestRequiresApproval:
    """Tests for the requires_approval escalation check."""

    @pytest.mark.unit
    def test_matching_prefix_returns_escalation(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(requires_approval=("deployment", "db")),
        )
        result = enforcer.check(
            "deploy_tool",
            ToolCategory.DEPLOYMENT,
            "deployment:staging",
            {},
        )
        assert result is not None
        assert result.requires_approval is True
        assert result.constraint == "requires_approval"

    @pytest.mark.unit
    def test_db_prefix_matches_db_mutate(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(requires_approval=("db",)),
        )
        result = enforcer.check(
            "sql_query", ToolCategory.DATABASE, ActionType.DB_MUTATE, {}
        )
        assert result is not None
        assert result.requires_approval is True

    @pytest.mark.unit
    def test_no_match_returns_none(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(requires_approval=("deployment",)),
        )
        result = enforcer.check(
            "read_file", ToolCategory.FILE_SYSTEM, ActionType.CODE_READ, {}
        )
        assert result is None

    @pytest.mark.unit
    def test_empty_requires_approval_allows_all(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(requires_approval=()),
        )
        result = enforcer.check(
            "deploy_tool",
            ToolCategory.DEPLOYMENT,
            "deployment:staging",
            {},
        )
        assert result is None


# ── Combined checks ────────────────────────────────────────────


class TestCombinedChecks:
    """Tests for multiple constraints evaluated together."""

    @pytest.mark.unit
    def test_first_violation_wins(self) -> None:
        """Network check runs before requires_approval."""
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(
                network=NetworkMode.NONE,
                requires_approval=("comms",),
            ),
        )
        result = enforcer.check(
            "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
        )
        assert result is not None
        # Network check fires first
        assert result.constraint == "network"

    @pytest.mark.unit
    def test_all_constraints_pass(self) -> None:
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(
                network=NetworkMode.OPEN,
                terminal=TerminalAccess.FULL,
                git=GitAccess.FULL,
            ),
        )
        result = enforcer.check(
            "read_file", ToolCategory.FILE_SYSTEM, ActionType.CODE_READ, {}
        )
        assert result is None

    @pytest.mark.unit
    def test_sandboxed_level_constraints(self) -> None:
        """Sandboxed: no network, no terminal, local-only git."""
        enforcer = SubConstraintEnforcer(
            ToolSubConstraints(
                file_system=FileSystemScope.WORKSPACE_ONLY,
                network=NetworkMode.NONE,
                git=GitAccess.LOCAL_ONLY,
                terminal=TerminalAccess.NONE,
            ),
        )
        # Web blocked
        assert (
            enforcer.check(
                "http_request", ToolCategory.WEB, ActionType.COMMS_EXTERNAL, {}
            )
            is not None
        )
        # Terminal blocked
        assert (
            enforcer.check(
                "shell_command", ToolCategory.TERMINAL, ActionType.CODE_WRITE, {}
            )
            is not None
        )
        # Git push blocked
        assert (
            enforcer.check(
                "git_push", ToolCategory.VERSION_CONTROL, ActionType.VCS_PUSH, {}
            )
            is not None
        )
        # File read allowed
        assert (
            enforcer.check(
                "read_file", ToolCategory.FILE_SYSTEM, ActionType.CODE_READ, {}
            )
            is None
        )
