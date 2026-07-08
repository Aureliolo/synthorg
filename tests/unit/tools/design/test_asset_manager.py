"""Tests for the asset manager tool."""

import pytest
from pydantic import ValidationError

from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools.design.asset_manager import AssetManagerTool
from tests._shared import JsonDict


@pytest.mark.unit
class TestAssetManagerTool:
    """Tests for AssetManagerTool."""

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("category", ToolCategory.DESIGN),
            ("action_type", ActionType.DOCS_WRITE),
            ("name", "asset_manager"),
        ],
        ids=["category", "action_type", "name"],
    )
    def test_tool_attributes(self, attr: str, expected: object) -> None:
        tool = AssetManagerTool()
        assert getattr(tool, attr) == expected

    async def test_list_empty(self) -> None:
        tool = AssetManagerTool()
        result = await tool.execute(arguments={"action": "list"})
        assert not result.is_error
        assert "No assets found" in result.content

    async def test_list_with_assets(self) -> None:
        tool = AssetManagerTool(
            assets={
                "img-001": {"type": "image", "tags": ["logo"]},
                "img-002": {"type": "image", "tags": ["banner"]},
            }
        )
        result = await tool.execute(arguments={"action": "list"})
        assert not result.is_error
        assert "2 asset(s)" in result.content
        assert "img-001" in result.content
        assert "img-002" in result.content

    async def test_list_filter_by_tags(self) -> None:
        tool = AssetManagerTool(
            assets={
                "img-001": {"type": "image", "tags": ["logo", "brand"]},
                "img-002": {"type": "image", "tags": ["banner"]},
            }
        )
        result = await tool.execute(arguments={"action": "list", "tags": ["logo"]})
        assert not result.is_error
        assert "1 asset(s)" in result.content
        assert "img-001" in result.content

    async def test_list_filter_by_tuple_tags(self) -> None:
        """Filter by a tuple ``tags`` value.

        ``ToolInvoker`` passes ``args_model.model_dump(mode="python")``,
        whose ``tags`` field is a tuple; tag filtering must apply rather
        than silently returning every asset.
        """
        tool = AssetManagerTool(
            assets={
                "img-001": {"type": "image", "tags": ["logo", "brand"]},
                "img-002": {"type": "image", "tags": ["banner"]},
            }
        )
        result = await tool.execute(
            arguments={"action": "list", "tags": ("logo",)},
        )
        assert not result.is_error
        assert "1 asset(s)" in result.content
        assert "img-001" in result.content

    async def test_get_existing_asset(self) -> None:
        tool = AssetManagerTool(
            assets={
                "img-001": {"type": "image", "width": 1024},
            }
        )
        result = await tool.execute(arguments={"action": "get", "asset_id": "img-001"})
        assert not result.is_error
        assert "img-001" in result.content
        assert result.metadata["type"] == "image"

    async def test_get_missing_asset(self) -> None:
        tool = AssetManagerTool()
        result = await tool.execute(arguments={"action": "get", "asset_id": "missing"})
        assert result.is_error
        assert "not found" in result.content

    async def test_get_without_asset_id(self) -> None:
        tool = AssetManagerTool()
        # ``AssetManagerArgs`` requires asset_id for get/delete at the
        # ``parse_typed`` boundary, so this raises rather than returning
        # an is_error result (the invoker maps it to invalid_argument).
        with pytest.raises(ValidationError):
            await tool.execute(arguments={"action": "get"})

    async def test_delete_existing_asset(self) -> None:
        tool = AssetManagerTool(assets={"img-001": {"type": "image"}})
        result = await tool.execute(
            arguments={"action": "delete", "asset_id": "img-001"}
        )
        assert not result.is_error
        assert "deleted" in result.content

        # Verify it's gone
        result2 = await tool.execute(arguments={"action": "get", "asset_id": "img-001"})
        assert result2.is_error

    async def test_delete_missing_asset(self) -> None:
        tool = AssetManagerTool()
        result = await tool.execute(
            arguments={"action": "delete", "asset_id": "missing"}
        )
        assert result.is_error
        assert "not found" in result.content

    async def test_delete_without_asset_id(self) -> None:
        tool = AssetManagerTool()
        with pytest.raises(ValidationError):
            await tool.execute(arguments={"action": "delete"})

    async def test_search_finds_matching(self) -> None:
        tool = AssetManagerTool(
            assets={
                "img-001": {"type": "image", "title": "Company Logo"},
                "img-002": {"type": "diagram", "title": "Architecture"},
            }
        )
        result = await tool.execute(arguments={"action": "search", "query": "logo"})
        assert not result.is_error
        assert "1 asset(s)" in result.content
        assert "img-001" in result.content

    async def test_search_no_results(self) -> None:
        tool = AssetManagerTool(assets={"img-001": {"type": "image"}})
        result = await tool.execute(
            arguments={"action": "search", "query": "nonexistent"}
        )
        assert not result.is_error
        assert "No assets matching" in result.content

    async def test_search_without_query(self) -> None:
        tool = AssetManagerTool()
        # ``AssetManagerArgs`` requires query for search at the
        # ``parse_typed`` boundary, so this raises rather than returning
        # an is_error result (the invoker maps it to invalid_argument).
        with pytest.raises(ValidationError):
            await tool.execute(arguments={"action": "search"})

    async def test_invalid_action(self) -> None:
        tool = AssetManagerTool()
        # ``action`` is a closed Literal on ``AssetManagerArgs``.
        with pytest.raises(ValidationError):
            await tool.execute(arguments={"action": "update"})

    def test_register_asset(self) -> None:
        tool = AssetManagerTool()
        tool.register_asset("img-001", {"type": "image"})
        assert tool._store.get("img-001") is not None

    async def test_register_then_get(self) -> None:
        tool = AssetManagerTool()
        tool.register_asset("img-001", {"type": "image", "size": 1024})
        result = await tool.execute(arguments={"action": "get", "asset_id": "img-001"})
        assert not result.is_error
        assert "img-001" in result.content

    def test_initial_assets_are_deep_copied(self) -> None:
        original: dict[str, JsonDict] = {"img-001": {"type": "image"}}
        tool = AssetManagerTool(assets=original)
        original["img-001"]["type"] = "modified"
        stored = tool._store.get("img-001")
        assert stored is not None
        assert stored["type"] == "image"
