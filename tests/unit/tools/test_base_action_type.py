"""Tests for BaseTool action_type integration with DEFAULT_CATEGORY_ACTION_MAP."""

from typing import Any

import pytest

from synthorg.core.enums import ActionType, ToolCategory
from synthorg.security.action_type_mapping import DEFAULT_CATEGORY_ACTION_MAP
from synthorg.tools.base import BaseTool, ToolExecutionResult

pytestmark = pytest.mark.timeout(30)


# ── Concrete test tool ───────────────────────────────────────────


class _ActionTypeTool(BaseTool):
    """Minimal concrete tool for testing action_type resolution."""

    def __init__(
        self,
        *,
        name: str = "action_type_tool",
        category: ToolCategory = ToolCategory.CODE_EXECUTION,
        action_type: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            category=category,
            action_type=action_type,
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


# ── Default action_type from category ────────────────────────────


@pytest.mark.unit
class TestDefaultActionTypeFromCategory:
    """action_type derived from category via DEFAULT_CATEGORY_ACTION_MAP."""

    @pytest.mark.parametrize(
        ("category", "expected_action_type"),
        [
            (ToolCategory.FILE_SYSTEM, ActionType.CODE_WRITE),
            (ToolCategory.CODE_EXECUTION, ActionType.CODE_WRITE),
            (ToolCategory.VERSION_CONTROL, ActionType.VCS_COMMIT),
            (ToolCategory.WEB, ActionType.COMMS_EXTERNAL),
            (ToolCategory.DATABASE, ActionType.DB_QUERY),
            (ToolCategory.TERMINAL, ActionType.CODE_WRITE),
            (ToolCategory.DESIGN, ActionType.DOCS_WRITE),
            (ToolCategory.COMMUNICATION, ActionType.COMMS_INTERNAL),
            (ToolCategory.ANALYTICS, ActionType.CODE_READ),
            (ToolCategory.DEPLOYMENT, ActionType.DEPLOY_STAGING),
            (ToolCategory.MCP, ActionType.CODE_WRITE),
            (ToolCategory.OTHER, ActionType.CODE_READ),
        ],
    )
    def test_default_action_type_matches_map(
        self,
        category: ToolCategory,
        expected_action_type: ActionType,
    ) -> None:
        """Each ToolCategory resolves to its mapped ActionType by default."""
        tool = _ActionTypeTool(category=category)
        assert tool.action_type == str(expected_action_type)

    def test_all_categories_covered_in_map(self) -> None:
        """Every ToolCategory member has an entry in DEFAULT_CATEGORY_ACTION_MAP."""
        for category in ToolCategory:
            assert category in DEFAULT_CATEGORY_ACTION_MAP, (
                f"ToolCategory.{category.name} missing from DEFAULT_CATEGORY_ACTION_MAP"
            )

    def test_action_type_property_returns_string(self) -> None:
        """action_type property returns a plain string."""
        tool = _ActionTypeTool(category=ToolCategory.FILE_SYSTEM)
        assert isinstance(tool.action_type, str)

    def test_default_action_type_is_str_of_enum(self) -> None:
        """Default action_type equals str() of the ActionType enum value."""
        tool = _ActionTypeTool(category=ToolCategory.DATABASE)
        expected = str(DEFAULT_CATEGORY_ACTION_MAP[ToolCategory.DATABASE])
        assert tool.action_type == expected


# ── Explicit action_type override ────────────────────────────────


@pytest.mark.unit
class TestExplicitActionTypeOverride:
    """Tests that explicit action_type overrides the category default."""

    def test_explicit_override_replaces_default(self) -> None:
        """Providing action_type bypasses the category-based lookup."""
        tool = _ActionTypeTool(
            category=ToolCategory.FILE_SYSTEM,
            action_type="custom:action",
        )
        # Default for FILE_SYSTEM is code:write — verify override
        assert tool.action_type == "custom:action"
        assert tool.action_type != str(ActionType.CODE_WRITE)

    def test_explicit_override_with_known_action_type(self) -> None:
        """Override with a known ActionType enum value string."""
        tool = _ActionTypeTool(
            category=ToolCategory.OTHER,
            action_type=str(ActionType.DEPLOY_PRODUCTION),
        )
        assert tool.action_type == "deploy:production"

    def test_explicit_empty_string_is_rejected(self) -> None:
        """Empty string fails the 'category:action' format validation."""
        with pytest.raises(ValueError, match="category:action"):
            _ActionTypeTool(
                category=ToolCategory.FILE_SYSTEM,
                action_type="",
            )

    def test_none_falls_back_to_default(self) -> None:
        """action_type=None (default) resolves from category map."""
        tool = _ActionTypeTool(
            category=ToolCategory.WEB,
            action_type=None,
        )
        assert tool.action_type == str(ActionType.COMMS_EXTERNAL)


# ── action_type property behavior ────────────────────────────────


@pytest.mark.unit
class TestActionTypeProperty:
    """Tests for the action_type property on BaseTool."""

    def test_property_is_stable(self) -> None:
        """Multiple accesses return the same value."""
        tool = _ActionTypeTool(category=ToolCategory.TERMINAL)
        first = tool.action_type
        second = tool.action_type
        assert first == second

    def test_property_on_different_tools_independent(self) -> None:
        """Two tools with different categories have different action_types."""
        tool_a = _ActionTypeTool(
            name="tool_a",
            category=ToolCategory.DATABASE,
        )
        tool_b = _ActionTypeTool(
            name="tool_b",
            category=ToolCategory.COMMUNICATION,
        )
        assert tool_a.action_type != tool_b.action_type
        assert tool_a.action_type == str(ActionType.DB_QUERY)
        assert tool_b.action_type == str(ActionType.COMMS_INTERNAL)

    def test_category_and_action_type_both_accessible(self) -> None:
        """Both category and action_type are exposed correctly."""
        tool = _ActionTypeTool(
            category=ToolCategory.VERSION_CONTROL,
            action_type="custom:vcs",
        )
        assert tool.category == ToolCategory.VERSION_CONTROL
        assert tool.action_type == "custom:vcs"
