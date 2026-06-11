"""Structural contract for the settings service.

Consumers (the config resolver, lifecycle wiring, setup controllers) depend on
the setting-value lifecycle surface, not on the concrete
:class:`~synthorg.settings.service.SettingsService` (which welds in the message
bus and encryptor). Annotating against this ``@runtime_checkable`` Protocol lets
them hold the service structurally, so the real class and the autospec test
doubles satisfy it. All signature types resolve at runtime; the protocol carries
no ``TYPE_CHECKING`` guard.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from synthorg.settings.enums import SettingsImportSource
from synthorg.settings.models import SettingDefinition, SettingEntry, SettingValue
from synthorg.settings.registry import SettingsRegistry


@runtime_checkable
class SettingsServiceProtocol(Protocol):
    """The setting-value lifecycle: resolve, page, version, write, delete."""

    @property
    def registry(self) -> SettingsRegistry:
        """The settings definition registry backing resolution."""
        ...

    async def get(self, namespace: str, key: str) -> SettingValue:
        """Resolve a single setting value via DB > env > default."""
        ...

    async def get_entry(self, namespace: str, key: str) -> SettingEntry:
        """Resolve a setting plus its definition and source metadata."""
        ...

    async def get_namespace(self, namespace: str) -> tuple[SettingEntry, ...]:
        """Resolve every entry in a namespace."""
        ...

    async def get_all(self) -> tuple[SettingEntry, ...]:
        """Resolve every registered setting."""
        ...

    async def get_page(
        self,
        *,
        after_key: str | None,
        limit: int,
    ) -> tuple[tuple[SettingEntry, ...], bool]:
        """Resolve one keyset page sorted by ``namespace:key``."""
        ...

    async def get_versioned(self, namespace: str, key: str) -> tuple[str, str]:
        """Read a value and its ``updated_at`` token for compare-and-set."""
        ...

    async def set(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        expected_updated_at: str | None = None,
        import_source: SettingsImportSource = SettingsImportSource.DIRECT_SET,
    ) -> SettingEntry:
        """Validate, encrypt, and persist a value with optional CAS."""
        ...

    async def set_many(
        self,
        items: Sequence[tuple[str, str, str]],
        *,
        expected_updated_at_map: Mapping[tuple[str, str], str],
        import_source: SettingsImportSource = SettingsImportSource.DIRECT_SET,
    ) -> str:
        """Atomically persist multiple values with per-key CAS."""
        ...

    async def delete(self, namespace: str, key: str) -> None:
        """Remove a setting's DB override."""
        ...

    async def delete_namespace(self, namespace: str) -> int:
        """Remove every DB override in a namespace; returns the count."""
        ...

    def get_schema(
        self,
        namespace: str | None = None,
    ) -> tuple[SettingDefinition, ...]:
        """Return the setting definitions, optionally scoped to a namespace."""
        ...
