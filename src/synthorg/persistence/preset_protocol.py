"""Repository protocol for custom personality preset persistence.

Each persistence domain has its own protocol module under
``src/synthorg/persistence/``.  Two naming conventions coexist:
``<domain>_protocol.py`` (the majority -- audit, project, settings,
task, ...) and ``<domain>_repo(s).py`` (this file plus
``ssrf_violation_repo.py``, ``workflow_definition_repo.py`` and
similar).  Both forms are equivalent; the variation is historical
and not worth normalising on its own.  This file is the preset
slice.
"""

from typing import NamedTuple, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001


class PresetRow(NamedTuple):
    """Single custom preset row returned by ``get``."""

    config_json: str
    description: str
    created_at: str
    updated_at: str


class PresetListRow(NamedTuple):
    """Custom preset row returned by ``list_all``."""

    name: NotBlankStr
    config_json: str
    description: str
    created_at: str
    updated_at: str


@runtime_checkable
class PersonalityPresetRepository(Protocol):
    """CRUD interface for user-defined personality preset persistence.

    Stores custom presets as JSON blobs alongside metadata.
    Builtin presets live in code and are never persisted here.
    """

    async def save(
        self,
        name: NotBlankStr,
        config_json: str,
        description: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        """Persist a custom preset (insert or update on conflict).

        Args:
            name: Lowercase preset identifier (primary key).
            config_json: Serialized ``PersonalityConfig`` as JSON.
            description: Human-readable description.
            created_at: ISO 8601 creation timestamp.
            updated_at: ISO 8601 last-update timestamp.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def get(
        self,
        name: NotBlankStr,
    ) -> PresetRow | None:
        """Retrieve a custom preset by name.

        Args:
            name: Preset identifier.

        Returns:
            A ``PresetRow`` or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def list_all(
        self,
    ) -> tuple[PresetListRow, ...]:
        """List all custom presets ordered by name.

        Returns:
            Tuple of ``PresetListRow`` named tuples.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def delete(self, name: NotBlankStr) -> bool:
        """Delete a custom preset by name.

        Args:
            name: Preset identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    async def count(self) -> int:
        """Return the number of stored custom presets.

        Raises:
            QueryError: If the operation fails.
        """
        ...
