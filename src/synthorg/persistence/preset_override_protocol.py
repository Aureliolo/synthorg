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

from synthorg.api.dto_provider_capabilities import PresetOverride  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


@runtime_checkable
class PresetOverrideRepo(Protocol):
    """Persistence interface for operator preset overrides.

    Implementations live under ``persistence/sqlite/`` and
    ``persistence/postgres/`` with a shared dual-backend conformance
    suite under ``tests/integration/persistence/``.
    """

    async def get(self, preset_name: NotBlankStr) -> PresetOverride | None:
        """Read the override for ``preset_name``, if any.

        Args:
            preset_name: Preset to read.

        Returns:
            The persisted override or ``None`` when no row exists.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def upsert(self, override: PresetOverride) -> PresetOverride:
        """Insert or replace the override for ``override.preset_name``.

        ``updated_at`` and ``updated_by`` are required by the schema;
        the service layer fills them before calling.

        Args:
            override: Full override row to persist.

        Returns:
            The persisted override (echo of input).

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    async def delete(self, preset_name: NotBlankStr) -> bool:
        """Remove the override for ``preset_name``.

        Args:
            preset_name: Preset whose override to remove.

        Returns:
            ``True`` when a row was removed, ``False`` when no row
            existed for the preset.

        Raises:
            QueryError: If the underlying DELETE fails.
        """
        ...
