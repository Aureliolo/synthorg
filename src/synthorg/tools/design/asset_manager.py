"""Asset manager tool -- manage generated design assets.

Provides CRUD operations on an in-memory asset registry that
tracks metadata for generated images, diagrams, and other
design artifacts.
"""

import copy
from typing import ClassVar, cast, override

from pydantic import BaseModel, JsonValue

from synthorg.core.boundary import parse_typed
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger
from synthorg.observability.events.design import (
    DESIGN_ASSET_DELETED,
    DESIGN_ASSET_LISTED,
    DESIGN_ASSET_RETRIEVED,
    DESIGN_ASSET_SEARCHED,
    DESIGN_ASSET_STORED,
    DESIGN_ASSET_VALIDATION_FAILED,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.design._args import AssetManagerArgs
from synthorg.tools.design.base_design_tool import BaseDesignTool
from synthorg.tools.design.config import DesignToolsConfig

logger = get_logger(__name__)


def _str_tags(meta: dict[str, JsonValue]) -> set[str]:
    """Return the string-valued ``tags`` entries of an asset's metadata.

    Asset metadata is an open JSON bag, so ``tags`` may be absent or a
    non-list value; this narrows it to the set of string tags.

    Returns:
        Set of string tags (empty if ``tags`` is missing or not a list).
    """
    raw = meta.get("tags")
    if not isinstance(raw, (list, tuple)):
        return set()
    return {tag for tag in raw if isinstance(tag, str)}


class AssetManagerTool(BaseDesignTool):
    """Manage generated design assets (list, get, delete, search).

    Maintains an in-memory registry of asset metadata.  Assets
    are registered by other design tools (e.g. ``ImageGeneratorTool``)
    and can be queried or removed through this tool.

    Examples:
        List all assets::

            tool = AssetManagerTool()
            result = await tool.execute(arguments={"action": "list"})

        Get a specific asset::

            result = await tool.execute(
                arguments={"action": "get", "asset_id": "img-001"}
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = AssetManagerArgs

    def __init__(
        self,
        *,
        config: DesignToolsConfig | None = None,
        assets: dict[str, dict[str, JsonValue]] | None = None,
    ) -> None:
        """Initialize the asset manager tool.

        Args:
            config: Design tool configuration. ``None`` falls back to
                defaults.
            assets: Pre-existing assets to seed the in-memory store.
                Deep-copied at construction; ``None`` starts empty.
        """
        super().__init__(
            name="asset_manager",
            description=("List, retrieve, delete, and search generated design assets."),
            parameters_schema=AssetManagerArgs.model_json_schema(),
            action_type=ActionType.DOCS_WRITE,
            config=config,
        )
        self._assets: dict[str, dict[str, JsonValue]] = (
            copy.deepcopy(assets) if assets else {}
        )

    def register_asset(
        self,
        asset_id: str,
        metadata: dict[str, JsonValue],
    ) -> None:
        """Register an asset in the internal registry.

        Called programmatically by other tools after generating
        an asset.

        Args:
            asset_id: Unique asset identifier.
            metadata: Asset metadata (type, dimensions, tags, etc.).

        Raises:
            ValueError: If asset_id is empty or whitespace-only.
        """
        if not asset_id.strip():
            msg = "asset_id must not be empty"
            raise ValueError(msg)
        self._assets[asset_id] = copy.deepcopy(metadata)
        logger.info(
            DESIGN_ASSET_STORED,
            asset_id=asset_id,
            asset_type=metadata.get("type", "unknown"),
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute an asset management operation.

        Args:
            arguments: Must contain ``action``; optionally
                ``asset_id``, ``tags``, ``query``.

        Returns:
            A ``ToolExecutionResult`` with operation results.
        """
        args = parse_typed("tool.execute", arguments, AssetManagerArgs)
        if args.action == "list":
            return self._handle_list(args)
        if args.action == "get":
            return self._handle_get(args)
        if args.action == "delete":
            return self._handle_delete(args)
        return self._handle_search(args)

    def _handle_list(
        self,
        args: AssetManagerArgs,
    ) -> ToolExecutionResult:
        """List assets, optionally filtered by tags.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        tags = list(args.tags)
        tag_set = set(tags)

        if tag_set:
            matching = {
                aid: meta
                for aid, meta in self._assets.items()
                if tag_set.issubset(_str_tags(meta))
            }
        else:
            matching = self._assets

        logger.info(
            DESIGN_ASSET_LISTED,
            total=len(self._assets),
            matched=len(matching),
            filter_tags=tags,
        )

        if not matching:
            return ToolExecutionResult(content="No assets found.")

        lines = [f"Found {len(matching)} asset(s):"]
        for aid, meta in sorted(matching.items()):
            asset_type = meta.get("type", "unknown")
            asset_tags = meta.get("tags", [])
            lines.append(f"  - {aid}: type={asset_type}, tags={asset_tags}")
        return ToolExecutionResult(
            content="\n".join(lines),
            metadata={"count": len(matching)},
        )

    def _handle_get(
        self,
        args: AssetManagerArgs,
    ) -> ToolExecutionResult:
        """Retrieve a specific asset by ID.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        # ``AssetManagerArgs._validate_action_fields`` guarantees asset_id is
        # present for get/delete; cast narrows the Optional for mypy.
        asset_id = cast("str", args.asset_id)
        meta = self._assets.get(asset_id)
        if meta is None:
            logger.warning(
                DESIGN_ASSET_VALIDATION_FAILED,
                action="get",
                reason="not_found",
                asset_id=asset_id,
            )
            return ToolExecutionResult(
                content=f"Asset not found: {asset_id!r}",
                is_error=True,
            )

        logger.info(
            DESIGN_ASSET_RETRIEVED,
            asset_id=asset_id,
        )

        lines = [f"Asset: {asset_id}"]
        for key, value in sorted(meta.items()):
            lines.append(f"  {key}: {value}")
        return ToolExecutionResult(
            content="\n".join(lines),
            metadata=copy.deepcopy(meta),
        )

    def _handle_delete(
        self,
        args: AssetManagerArgs,
    ) -> ToolExecutionResult:
        """Delete an asset by ID.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        # Guaranteed present for delete by the model validator; cast for mypy.
        asset_id = cast("str", args.asset_id)
        if asset_id not in self._assets:
            logger.warning(
                DESIGN_ASSET_VALIDATION_FAILED,
                action="delete",
                reason="not_found",
                asset_id=asset_id,
            )
            return ToolExecutionResult(
                content=f"Asset not found: {asset_id!r}",
                is_error=True,
            )

        del self._assets[asset_id]

        logger.info(
            DESIGN_ASSET_DELETED,
            asset_id=asset_id,
        )

        return ToolExecutionResult(
            content=f"Asset deleted: {asset_id}",
        )

    def _handle_search(
        self,
        args: AssetManagerArgs,
    ) -> ToolExecutionResult:
        """Search assets by query string in metadata values.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        # Guaranteed present for search by the model validator; cast for mypy.
        query = normalize_ascii_lowercase(cast("str", args.query))
        tags = list(args.tags)
        tag_set = set(tags)

        matching: dict[str, dict[str, JsonValue]] = {}
        for aid, meta in self._assets.items():
            searchable = " ".join(str(v).lower() for v in meta.values())
            if query not in searchable:
                continue
            if tag_set and not tag_set.issubset(_str_tags(meta)):
                continue
            matching[aid] = meta

        logger.info(
            DESIGN_ASSET_SEARCHED,
            total=len(self._assets),
            matched=len(matching),
            search_query=query,
            filter_tags=tags,
        )

        if not matching:
            return ToolExecutionResult(
                content=f"No assets matching query: {query!r}",
            )

        lines = [f"Found {len(matching)} asset(s) matching {query!r}:"]
        for aid, meta in sorted(matching.items()):
            asset_type = meta.get("type", "unknown")
            lines.append(f"  - {aid}: type={asset_type}")
        return ToolExecutionResult(
            content="\n".join(lines),
            metadata={"count": len(matching)},
        )
