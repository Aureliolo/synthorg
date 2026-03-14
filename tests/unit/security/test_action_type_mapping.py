"""Tests for DEFAULT_CATEGORY_ACTION_MAP (ToolCategory → ActionType mapping)."""

import pytest

from synthorg.core.enums import ActionType, ToolCategory
from synthorg.security.action_type_mapping import DEFAULT_CATEGORY_ACTION_MAP

pytestmark = pytest.mark.timeout(30)


# -- Coverage: every ToolCategory has a mapping --------------------------------


@pytest.mark.unit
class TestMappingCompleteness:
    def test_every_tool_category_has_entry(self) -> None:
        for category in ToolCategory:
            assert category in DEFAULT_CATEGORY_ACTION_MAP, (
                f"ToolCategory.{category.name} missing from DEFAULT_CATEGORY_ACTION_MAP"
            )

    def test_mapping_has_exactly_12_entries(self) -> None:
        assert len(DEFAULT_CATEGORY_ACTION_MAP) == len(ToolCategory)

    def test_no_extra_keys_beyond_tool_category(self) -> None:
        tool_category_values = set(ToolCategory)
        for key in DEFAULT_CATEGORY_ACTION_MAP:
            assert key in tool_category_values


# -- Values are valid ActionType members ---------------------------------------


@pytest.mark.unit
class TestMappingValues:
    def test_all_values_are_action_type_members(self) -> None:
        for category, action in DEFAULT_CATEGORY_ACTION_MAP.items():
            assert isinstance(action, ActionType), (
                f"Value for {category.name} is {type(action).__name__}, "
                f"expected ActionType"
            )

    def test_values_use_colon_format(self) -> None:
        for action in DEFAULT_CATEGORY_ACTION_MAP.values():
            assert ":" in action.value


# -- Spot-check specific mappings ----------------------------------------------


@pytest.mark.unit
class TestMappingSpotChecks:
    @pytest.mark.parametrize(
        ("category", "expected_action"),
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
    def test_category_maps_to_expected_action(
        self,
        category: ToolCategory,
        expected_action: ActionType,
    ) -> None:
        assert DEFAULT_CATEGORY_ACTION_MAP[category] is expected_action


# -- Immutability --------------------------------------------------------------


@pytest.mark.unit
class TestMappingImmutability:
    def test_mapping_is_read_only(self) -> None:
        with pytest.raises(TypeError):
            DEFAULT_CATEGORY_ACTION_MAP[ToolCategory.FILE_SYSTEM] = ActionType.CODE_READ  # type: ignore[index]

    def test_cannot_delete_entry(self) -> None:
        with pytest.raises(TypeError):
            del DEFAULT_CATEGORY_ACTION_MAP[ToolCategory.FILE_SYSTEM]  # type: ignore[attr-defined]
