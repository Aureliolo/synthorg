"""Tests for ToolPermissionChecker."""

from typing import Any

import pytest

from synthorg.core.agent import ToolPermissions
from synthorg.core.enums import ToolAccessLevel, ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.errors import ToolPermissionDeniedError
from synthorg.tools.permissions import ToolPermissionChecker
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.timeout(30)


# ── Local test tool ──────────────────────────────────────────────


class _SimpleTool(BaseTool):
    """Minimal tool with configurable name and category."""

    def __init__(
        self,
        *,
        name: str,
        category: ToolCategory = ToolCategory.OTHER,
    ) -> None:
        super().__init__(
            name=name,
            description=f"Test tool: {name}",
            category=category,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


# ── Access level category tests ──────────────────────────────────


@pytest.mark.unit
class TestAccessLevelCategories:
    """Parametrized tests for Sandboxed, Restricted, and Standard levels."""

    @pytest.mark.parametrize(
        ("access_level", "category", "expected"),
        [
            # Sandboxed: allows file_system, code_execution, version_control
            (ToolAccessLevel.SANDBOXED, ToolCategory.FILE_SYSTEM, True),
            (ToolAccessLevel.SANDBOXED, ToolCategory.CODE_EXECUTION, True),
            (ToolAccessLevel.SANDBOXED, ToolCategory.VERSION_CONTROL, True),
            (ToolAccessLevel.SANDBOXED, ToolCategory.WEB, False),
            (ToolAccessLevel.SANDBOXED, ToolCategory.TERMINAL, False),
            (ToolAccessLevel.SANDBOXED, ToolCategory.DEPLOYMENT, False),
            (ToolAccessLevel.SANDBOXED, ToolCategory.DATABASE, False),
            # Restricted: adds web
            (ToolAccessLevel.RESTRICTED, ToolCategory.FILE_SYSTEM, True),
            (ToolAccessLevel.RESTRICTED, ToolCategory.CODE_EXECUTION, True),
            (ToolAccessLevel.RESTRICTED, ToolCategory.VERSION_CONTROL, True),
            (ToolAccessLevel.RESTRICTED, ToolCategory.WEB, True),
            (ToolAccessLevel.RESTRICTED, ToolCategory.TERMINAL, False),
            (ToolAccessLevel.RESTRICTED, ToolCategory.ANALYTICS, False),
            # Standard: adds terminal and analytics
            (ToolAccessLevel.STANDARD, ToolCategory.FILE_SYSTEM, True),
            (ToolAccessLevel.STANDARD, ToolCategory.WEB, True),
            (ToolAccessLevel.STANDARD, ToolCategory.TERMINAL, True),
            (ToolAccessLevel.STANDARD, ToolCategory.ANALYTICS, True),
            (ToolAccessLevel.STANDARD, ToolCategory.DEPLOYMENT, False),
            (ToolAccessLevel.STANDARD, ToolCategory.DATABASE, False),
            (ToolAccessLevel.STANDARD, ToolCategory.OTHER, False),
        ],
        ids=lambda p: p.value if hasattr(p, "value") else str(p),
    )
    def test_category_permission(
        self,
        access_level: ToolAccessLevel,
        category: ToolCategory,
        expected: bool,
    ) -> None:
        checker = ToolPermissionChecker(access_level=access_level)
        assert checker.is_permitted("t", category) is expected


@pytest.mark.unit
class TestElevatedLevel:
    """Elevated allows all categories."""

    def test_allows_all_categories(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.ELEVATED)
        for cat in ToolCategory:
            assert checker.is_permitted(f"tool_{cat.value}", cat) is True


@pytest.mark.unit
class TestCustomLevel:
    """Custom denies everything unless in allowed list."""

    def test_denies_all_categories(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.CUSTOM)
        for cat in ToolCategory:
            assert checker.is_permitted(f"tool_{cat.value}", cat) is False


# ── Denied list tests ────────────────────────────────────────────


@pytest.mark.unit
class TestDeniedList:
    """Denied list always takes priority."""

    def test_denied_overrides_category_access(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
            denied=frozenset({"dangerous_tool"}),
        )
        assert checker.is_permitted("dangerous_tool", ToolCategory.FILE_SYSTEM) is False

    def test_denied_overrides_allowed_list(self) -> None:
        """Belt-and-suspenders: denied wins even if tool is also in allowed."""
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.CUSTOM,
            allowed=frozenset({"tool_x"}),
            denied=frozenset({"tool_x"}),
        )
        assert checker.is_permitted("tool_x", ToolCategory.OTHER) is False


# ── Allowed list tests ───────────────────────────────────────────


@pytest.mark.unit
class TestAllowedList:
    """Allowed list grants access regardless of level."""

    def test_allowed_overrides_access_level(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
            allowed=frozenset({"deploy_tool"}),
        )
        assert checker.is_permitted("deploy_tool", ToolCategory.DEPLOYMENT) is True

    def test_allowed_works_with_custom_level(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.CUSTOM,
            allowed=frozenset({"my_tool"}),
        )
        assert checker.is_permitted("my_tool", ToolCategory.DATABASE) is True

    def test_custom_with_empty_allowed_denies_all(self) -> None:
        checker = ToolPermissionChecker(access_level=ToolAccessLevel.CUSTOM)
        assert checker.is_permitted("any_tool", ToolCategory.FILE_SYSTEM) is False


# ── Resolution priority tests ────────────────────────────────────


@pytest.mark.unit
class TestResolutionPriority:
    """Full resolution priority: denied > allowed > level categories."""

    def test_denied_then_allowed_then_level(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.STANDARD,
            allowed=frozenset({"special"}),
            denied=frozenset({"blocked"}),
        )
        # denied wins
        assert checker.is_permitted("blocked", ToolCategory.FILE_SYSTEM) is False
        # allowed overrides level
        assert checker.is_permitted("special", ToolCategory.DEPLOYMENT) is True
        # falls through to level
        assert checker.is_permitted("regular", ToolCategory.TERMINAL) is True
        assert checker.is_permitted("other", ToolCategory.DEPLOYMENT) is False

    def test_custom_level_with_allowed_list(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.CUSTOM,
            allowed=frozenset({"tool_a", "tool_b"}),
        )
        assert checker.is_permitted("tool_a", ToolCategory.OTHER) is True
        assert checker.is_permitted("tool_b", ToolCategory.DEPLOYMENT) is True
        assert checker.is_permitted("tool_c", ToolCategory.FILE_SYSTEM) is False


# ── denial_reason tests ──────────────────────────────────────────


@pytest.mark.unit
class TestDenialReason:
    """Human-readable denial reasons."""

    def test_denied_list_reason(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
            denied=frozenset({"bad_tool"}),
        )
        reason = checker.denial_reason("bad_tool", ToolCategory.FILE_SYSTEM)
        assert "denied" in reason.lower()
        assert "bad_tool" in reason

    def test_category_not_permitted_reason(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
        )
        reason = checker.denial_reason("web_tool", ToolCategory.WEB)
        assert "web" in reason.lower()
        assert "sandboxed" in reason.lower()

    def test_custom_not_in_allowed_reason(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.CUSTOM,
        )
        reason = checker.denial_reason("some_tool", ToolCategory.OTHER)
        assert "custom" in reason.lower()
        assert "allowed" in reason.lower()


# ── filter_definitions tests ─────────────────────────────────────


@pytest.mark.unit
class TestFilterDefinitions:
    """Definition filtering against a registry."""

    def test_filters_to_permitted_only(self) -> None:
        tools = [
            _SimpleTool(name="fs_tool", category=ToolCategory.FILE_SYSTEM),
            _SimpleTool(name="web_tool", category=ToolCategory.WEB),
            _SimpleTool(name="deploy_tool", category=ToolCategory.DEPLOYMENT),
        ]
        registry = ToolRegistry(tools)
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
        )
        filtered = checker.filter_definitions(registry)
        names = {d.name for d in filtered}
        assert "fs_tool" in names
        assert "web_tool" not in names
        assert "deploy_tool" not in names

    def test_empty_registry(self) -> None:
        registry = ToolRegistry([])
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
        )
        assert checker.filter_definitions(registry) == ()

    def test_all_permitted_returns_all(self) -> None:
        tools = [
            _SimpleTool(name="tool_a", category=ToolCategory.FILE_SYSTEM),
            _SimpleTool(name="tool_b", category=ToolCategory.WEB),
        ]
        registry = ToolRegistry(tools)
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
        )
        filtered = checker.filter_definitions(registry)
        assert len(filtered) == 2

    def test_filter_respects_allowed_list(self) -> None:
        tools = [
            _SimpleTool(name="special", category=ToolCategory.DEPLOYMENT),
            _SimpleTool(name="normal", category=ToolCategory.DEPLOYMENT),
        ]
        registry = ToolRegistry(tools)
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
            allowed=frozenset({"special"}),
        )
        filtered = checker.filter_definitions(registry)
        names = {d.name for d in filtered}
        assert "special" in names
        assert "normal" not in names

    def test_filter_respects_denied_list(self) -> None:
        """Explicitly denied tool is excluded even if its category is permitted."""
        tools = [
            _SimpleTool(name="fs_tool", category=ToolCategory.FILE_SYSTEM),
            _SimpleTool(name="blocked_fs", category=ToolCategory.FILE_SYSTEM),
        ]
        registry = ToolRegistry(tools)
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
            denied=frozenset({"blocked_fs"}),
        )
        filtered = checker.filter_definitions(registry)
        names = {d.name for d in filtered}
        assert "fs_tool" in names
        assert "blocked_fs" not in names

    def test_filter_returns_sorted_by_name(self) -> None:
        """Filtered definitions are sorted by tool name."""
        tools = [
            _SimpleTool(name="zeta", category=ToolCategory.FILE_SYSTEM),
            _SimpleTool(name="alpha", category=ToolCategory.FILE_SYSTEM),
            _SimpleTool(name="mid", category=ToolCategory.FILE_SYSTEM),
        ]
        registry = ToolRegistry(tools)
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
        )
        filtered = checker.filter_definitions(registry)
        names = [d.name for d in filtered]
        assert names == sorted(names)


# ── Edge cases ───────────────────────────────────────────────────


@pytest.mark.unit
class TestEdgeCases:
    """Edge cases for permission checking."""

    def test_case_insensitive_matching_allowed(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.CUSTOM,
            allowed=frozenset({"Echo"}),
        )
        assert checker.is_permitted("echo", ToolCategory.OTHER) is True
        assert checker.is_permitted("ECHO", ToolCategory.OTHER) is True
        assert checker.is_permitted("Echo", ToolCategory.OTHER) is True

    def test_case_insensitive_matching_denied(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.ELEVATED,
            denied=frozenset({"Blocked"}),
        )
        assert checker.is_permitted("blocked", ToolCategory.FILE_SYSTEM) is False
        assert checker.is_permitted("BLOCKED", ToolCategory.FILE_SYSTEM) is False

    def test_empty_allowed_and_denied(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.STANDARD,
        )
        assert checker.is_permitted("tool", ToolCategory.FILE_SYSTEM) is True

    def test_from_permissions_factory(self) -> None:
        perms = ToolPermissions(
            access_level=ToolAccessLevel.RESTRICTED,
            allowed=("special_tool",),
            denied=("blocked_tool",),
        )
        checker = ToolPermissionChecker.from_permissions(perms)
        assert checker.is_permitted("special_tool", ToolCategory.DEPLOYMENT) is True
        assert checker.is_permitted("blocked_tool", ToolCategory.FILE_SYSTEM) is False
        assert checker.is_permitted("other", ToolCategory.WEB) is True

    def test_check_raises_on_denied(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
        )
        with pytest.raises(ToolPermissionDeniedError):
            checker.check("tool", ToolCategory.DEPLOYMENT)

    def test_check_error_context_dict(self) -> None:
        """ToolPermissionDeniedError carries correct context keys."""
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
        )
        with pytest.raises(ToolPermissionDeniedError) as exc_info:
            checker.check("my_tool", ToolCategory.DEPLOYMENT)
        assert exc_info.value.context["tool"] == "my_tool"
        assert exc_info.value.context["category"] == "deployment"

    def test_check_passes_on_permitted(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.SANDBOXED,
        )
        checker.check("tool", ToolCategory.FILE_SYSTEM)  # should not raise

    def test_whitespace_in_name_normalized(self) -> None:
        checker = ToolPermissionChecker(
            access_level=ToolAccessLevel.CUSTOM,
            allowed=frozenset({"  my_tool  "}),
        )
        assert checker.is_permitted("my_tool", ToolCategory.OTHER) is True
