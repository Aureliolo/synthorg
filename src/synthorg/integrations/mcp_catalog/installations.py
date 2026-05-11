"""MCP catalog installations.

Records catalog entries that the dashboard has installed, keyed by
catalog entry id. Persists out-of-band from the user-owned YAML
config so installs survive restarts without touching the file, and
so the MCP bridge can merge these rows into its effective server
list at startup (see :mod:`synthorg.integrations.mcp_catalog.install`).

The primary key on ``catalog_entry_id`` makes install idempotent:
re-installing the same entry is a safe upsert that refreshes
``installed_at`` and overwrites the associated ``connection_name``.
"""

from typing import Final, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr  # noqa: TC001

_DEFAULT_LIMIT: Final[int] = 100


class McpInstallation(BaseModel):
    """A recorded MCP catalog installation.

    Attributes:
        catalog_entry_id: Unique id of the installed catalog entry.
        connection_name: Name of the bound connection, or ``None``
            for connectionless servers (filesystem, memory, etc.).
        installed_at: When the install was recorded.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    catalog_entry_id: NotBlankStr
    connection_name: NotBlankStr | None = None
    installed_at: AwareDatetime


@runtime_checkable
class McpInstallationRepository(Protocol):
    """CRUD interface for MCP catalog installations."""

    async def save(self, installation: McpInstallation) -> None:
        """Upsert an installation (idempotent on catalog_entry_id)."""
        ...

    async def get(self, catalog_entry_id: NotBlankStr) -> McpInstallation | None:
        """Fetch an installation by catalog entry id."""
        ...

    async def list_items(
        self,
        *,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[McpInstallation, ...]:
        """List recorded installations.

        ``limit`` defaults to the protocol-wide pagination floor; pass
        a larger ``limit`` or loop with ``offset`` for cursor-style
        pagination. Implementations enforce ``limit >= 1`` /
        ``offset >= 0`` via the shared ``validate_pagination_args``
        helper and raise ``QueryError`` on invalid inputs.
        """
        ...

    async def delete(self, catalog_entry_id: NotBlankStr) -> bool:
        """Delete an installation.

        Returns:
            ``True`` if a row was deleted.
        """
        ...
