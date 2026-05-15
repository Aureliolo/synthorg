"""Persistence protocol for operator overrides on provider presets.

Preset overrides are operator-authored deltas on top of the in-code
:class:`synthorg.providers.presets.CloudPreset` /
:class:`synthorg.providers.presets.LocalPreset` definitions.  When an
override row exists for a preset, its non-``None`` fields replace the
preset's corresponding fields at read time
(:class:`PresetOverrideService.get_effective`); ``None`` fields fall
back to the in-code preset.

Storage is keyed by ``preset_name`` (one row per preset, no history).
The retention sweeper exception that ``ProviderAuditRepo`` carries
does not apply here -- overrides are operator state and survive until
explicitly cleared with ``delete``.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import IdKeyedRepository
from synthorg.providers.management.capability_dtos import PresetOverride


@runtime_checkable
class PresetOverrideRepo(
    IdKeyedRepository[PresetOverride, NotBlankStr],
    Protocol,
):
    """Persistence interface for operator preset overrides.

    Composes :class:`IdKeyedRepository` (ADR-0001): the natural key is
    ``preset_name``. Implementations live under ``persistence/sqlite/``
    and ``persistence/postgres/`` with a shared dual-backend
    conformance suite under ``tests/integration/persistence/``.
    """

    async def get(self, entity_id: NotBlankStr) -> PresetOverride | None:
        """Read the override for ``entity_id``, if any.

        Args:
            entity_id: Preset name whose override to read.

        Returns:
            The persisted override or ``None`` when no row exists.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def save(self, entity: PresetOverride) -> None:
        """Insert or replace the override for ``entity.preset_name``.

        ``updated_at`` and ``updated_by`` are required by the schema;
        the service layer fills them before calling.

        Args:
            entity: Full override row to persist.

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Remove the override for ``entity_id``.

        Args:
            entity_id: Preset name whose override to remove.

        Returns:
            ``True`` when a row was removed, ``False`` when no row
            existed for the preset.

        Raises:
            QueryError: If the underlying DELETE fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- canonical ADR-0001 page size
        offset: int = 0,
    ) -> tuple[PresetOverride, ...]:
        """List overrides ordered by ``preset_name`` ascending.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Paginated overrides in ascending preset-name order.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...
