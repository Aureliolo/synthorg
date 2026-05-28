"""Asset manager tool -- manage generated design assets.

Provides CRUD operations on an in-memory asset registry that
tracks metadata for generated images, diagrams, and other
design artifacts.
"""

import copy
from typing import Any, ClassVar, Final

from pydantic import BaseModel

from synthorg.core.enums import ActionType
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
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.design._args import AssetManagerArgs
from synthorg.tools.design.base_design_tool import BaseDesignTool
from synthorg.tools.design.config import DesignToolsConfig

logger = get_logger(__name__)

_VALID_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "list",
        "get",
        "delete",
        "search",
    }
)

_PARAMETERS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": sorted(_VALID_ACTIONS),
            "description": "Asset operation to perform",
        },
        "asset_id": {
            "type": "string",
            "description": "Asset identifier (required for get/delete)",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tags for filtering (used with list/search)",
        },
        "query": {
            "type": "string",
            "description": "Search query for asset metadata",
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}


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
        assets: dict[str, dict[str, Any]] | None = None,
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
        self._assets: dict[str, dict[str, Any]] = (
            copy.deepcopy(assets) if assets else {}
        )

    def register_asset(
        self,
        asset_id: str,
        metadata: dict[str, Any],
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

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute an asset management operation.

        Args:
            arguments: Must contain ``action``; optionally
                ``asset_id``, ``tags``, ``query``.

        Returns:
            A ``ToolExecutionResult`` with operation results.
        """
        action = arguments.get("action")
        if not isinstance(action, str):
            logger.warning(
                DESIGN_ASSET_VALIDATION_FAILED,
                reason="missing_action",
            )
            return ToolExecutionResult(
                content="'action' is required and must be a string.",
                is_error=True,
            )

        if action not in _VALID_ACTIONS:
            logger.warning(
                DESIGN_ASSET_VALIDATION_FAILED,
                action=action,
                reason="invalid_action",
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid action: {action!r}. "
                    f"Must be one of: {sorted(_VALID_ACTIONS)}"
                ),
                is_error=True,
            )

        if action == "list":
            return self._handle_list(arguments)
        if action == "get":
            return self._handle_get(arguments)
        if action == "delete":
            return self._handle_delete(arguments)
        return self._handle_search(arguments)

    def _handle_list(
        self,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """List assets, optionally filtered by tags.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        raw_tags = arguments.get("tags")
        if raw_tags is not None and not isinstance(raw_tags, list):
            logger.debug(
                DESIGN_ASSET_VALIDATION_FAILED,
                action="list",
                reason="invalid_tags_type",
            )
        raw_list = raw_tags if isinstance(raw_tags, list) else []
        tags = [t for t in raw_list if isinstance(t, str)]
        tag_set = set(tags)

        if tag_set:
            matching = {
                aid: meta
                for aid, meta in self._assets.items()
                if tag_set.issubset(
                    {t for t in (meta.get("tags") or []) if isinstance(t, str)}
                )
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
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Retrieve a specific asset by ID.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        asset_id = arguments.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            logger.warning(
                DESIGN_ASSET_VALIDATION_FAILED,
                action="get",
                reason="missing_asset_id",
            )
            return ToolExecutionResult(
                content="asset_id is required for 'get' action.",
                is_error=True,
            )

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
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Delete an asset by ID.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        asset_id = arguments.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            logger.warning(
                DESIGN_ASSET_VALIDATION_FAILED,
                action="delete",
                reason="missing_asset_id",
            )
            return ToolExecutionResult(
                content="asset_id is required for 'delete' action.",
                is_error=True,
            )

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
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Search assets by query string in metadata values.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        raw_query = arguments.get("query")
        if not isinstance(raw_query, str) or not raw_query.strip():
            logger.warning(
                DESIGN_ASSET_VALIDATION_FAILED,
                action="search",
                reason="missing_query",
            )
            return ToolExecutionResult(
                content="query is required for 'search' action.",
                is_error=True,
            )

        query = normalize_ascii_lowercase(raw_query)
        raw_tags = arguments.get("tags")
        raw_list = raw_tags if isinstance(raw_tags, list) else []
        tags = [t for t in raw_list if isinstance(t, str)]
        tag_set = set(tags)

        matching: dict[str, dict[str, Any]] = {}
        for aid, meta in self._assets.items():
            searchable = " ".join(str(v).lower() for v in meta.values())
            if query not in searchable:
                continue
            if tag_set and not tag_set.issubset(
                {t for t in (meta.get("tags") or []) if isinstance(t, str)}
            ):
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
