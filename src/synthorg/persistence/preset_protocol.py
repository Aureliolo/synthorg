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

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class Preset(BaseModel):
    """A custom personality preset entity.

    Attributes:
        name: Lowercase preset identifier (primary key).
        config_json: Serialized ``PersonalityConfig`` as JSON.
        description: Human-readable description.
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last-update timestamp.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr
    config_json: str
    description: str
    created_at: str
    updated_at: str


class PresetFilterSpec(BaseModel):
    """Filter spec for ``PersonalityPresetRepository.query`` (ADR-0001).

    Currently empty (no filtering criteria), but reserved for future
    expansion (e.g., filter by creation time, description keywords).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


@runtime_checkable
class PersonalityPresetRepository(
    IdKeyedRepository[Preset, NotBlankStr],
    FilteredQueryRepository[Preset, PresetFilterSpec],
    Protocol,
):
    """CRUD + query interface for custom personality preset persistence.

    Stores custom presets as JSON blobs alongside metadata.
    Builtin presets live in code and are never persisted here.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`
    (ADR-0001).
    """

    @override
    async def save(self, entity: Preset) -> None:
        """Persist a custom preset (insert or update by name).

        Args:
            entity: The preset to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> Preset | None:
        """Retrieve a custom preset by name.

        Args:
            entity_id: Preset identifier (name).

        Returns:
            A ``Preset`` or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Preset, ...]:
        """List custom presets ordered by name.

        Args:
            limit: Maximum presets to return.
            offset: Rows to skip before the window.

        Returns:
            Presets in ascending ``name`` order.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: PresetFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Preset, ...]:
        """List custom presets matching the filter spec.

        Args:
            filter_spec: Filter criteria (currently unused, reserved for future).
            limit: Maximum presets to return.
            offset: Rows to skip before the window.

        Returns:
            Presets in ascending ``name`` order.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: PresetFilterSpec) -> int:
        """Count custom presets matching the filter spec.

        Args:
            filter_spec: Filter criteria (currently unused, reserved for future).

        Returns:
            Number of matching presets.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a custom preset by name.

        Args:
            entity_id: Preset identifier (name).

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
