# ruff: noqa: EM101
# module-kind: service
"""MCP catalog facade over ``CatalogService`` + installation repo."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    MCP_CATALOG_INSTALLED_VIA_MCP,
    MCP_CATALOG_UNINSTALLED_VIA_MCP,
)

if TYPE_CHECKING:
    # CatalogService / McpInstallationRepository are concrete collaborators
    # injected via SimpleNamespace fakes in tests; a runtime import would make
    # typeguard reject the fakes.
    from synthorg.integrations.mcp_catalog.installations import (
        McpInstallationRepository,
    )
    from synthorg.integrations.mcp_catalog.service import CatalogService

logger = get_logger(__name__)


class MCPCatalogFacadeService:
    """Facade over :class:`CatalogService` + installation repo."""

    def __init__(
        self,
        *,
        catalog: CatalogService,
        installations: McpInstallationRepository,
    ) -> None:
        self._catalog = cast("object", catalog)
        self._installations = cast("object", installations)

    async def list_catalog(self) -> Sequence[object]:
        """List all catalog entries.

        Returns:
            A tuple of all catalog entries.

        Raises:
            CapabilityNotSupportedError: If the backing ``CatalogService``
                does not expose ``list_entries``.
        """
        fn = getattr(self._catalog, "list_entries", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_list",
                "CatalogService does not expose list_entries",
            )
        return tuple(await fn())

    async def search_catalog(
        self,
        query: NotBlankStr,
    ) -> Sequence[object]:
        """Search catalog entries by query string.

        Returns:
            A tuple of catalog entries matching the query.

        Raises:
            CapabilityNotSupportedError: If the backing ``CatalogService``
                does not expose ``search``.
        """
        fn = getattr(self._catalog, "search", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_search",
                "CatalogService does not expose search",
            )
        return tuple(await fn(query))

    async def get_catalog_entry(
        self,
        entry_id: NotBlankStr,
    ) -> object | None:
        """Fetch a single catalog entry by ID.

        Returns:
            The matching catalog entry, or ``None`` when no entry has the
            given ID.

        Raises:
            CapabilityNotSupportedError: If the backing ``CatalogService``
                does not expose ``get_entry``.
        """
        fn = getattr(self._catalog, "get_entry", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_get",
                "CatalogService does not expose get_entry",
            )
        return cast("object | None", await fn(entry_id))

    async def install_catalog_entry(
        self,
        *,
        entry_id: NotBlankStr,
        actor_id: NotBlankStr,
    ) -> object:
        """Install a catalog entry and audit the event.

        Returns:
            The installation record returned by the repository.

        Raises:
            CapabilityNotSupportedError: If the installation repository
                does not expose ``install``.
        """
        fn = getattr(self._installations, "install", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_install",
                "McpInstallationRepository does not expose install",
            )
        result = await fn(entry_id=entry_id, actor=actor_id)
        logger.info(
            MCP_CATALOG_INSTALLED_VIA_MCP,
            entry_id=entry_id,
            actor_id=actor_id,
        )
        return result

    async def uninstall_catalog_entry(
        self,
        *,
        installation_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Uninstall a catalog entry and audit the event.

        Returns:
            ``True`` when an installation row was removed; ``False`` on a
            miss.

        Raises:
            CapabilityNotSupportedError: If the installation repository
                does not expose ``uninstall``.
        """
        fn = getattr(self._installations, "uninstall", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_uninstall",
                "McpInstallationRepository does not expose uninstall",
            )
        removed = bool(await fn(installation_id=installation_id))
        if removed:
            logger.info(
                MCP_CATALOG_UNINSTALLED_VIA_MCP,
                installation_id=installation_id,
                actor_id=actor_id,
                reason=reason,
            )
        return removed
