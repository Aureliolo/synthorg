"""Whole-table scans over persisted setting overrides.

Only explicitly-overridden settings have a row, so the whole override table
is small enough to read in one page and answer questions the per-key
resolution path cannot: which writes the running process has not read.
"""

from datetime import datetime
from typing import Final

from synthorg.core.iso_datetime import parse_iso_utc
from synthorg.core.types import NotBlankStr
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings.models import PendingRestartSetting
from synthorg.settings.registry import SettingsRegistry

# One page covers every deployment; the bound exists so a corrupted table
# cannot stream unboundedly.
ALL_OVERRIDES_LIMIT: Final[int] = 10_000


async def collect_pending_restart(
    repository: SettingsRepository,
    registry: SettingsRegistry,
    *,
    since: datetime,
) -> tuple[PendingRestartSetting, ...]:
    """Find restart-required settings written after ``since``.

    Derived, never stored: a restart-required value is read once at boot, so
    a row written after the process started is by definition not in effect
    yet, and the process coming back empties the answer without anything
    having to clear a flag.

    That "read once at boot" holds because a ``restart_required`` definition
    is deliberately excluded from the live settings dispatcher, which
    ``scripts/check_setting_restart_required_justified.py`` enforces per key.
    This module cannot prove it alone, so the guarantee is worth naming.

    Args:
        repository: Settings repository to scan.
        registry: Definition registry deciding which keys need a restart.
        since: The process's boot instant.

    Returns:
        The pending settings, sorted by namespace then key.
    """
    rows = await repository.list_items(limit=ALL_OVERRIDES_LIMIT)
    pending: list[PendingRestartSetting] = []
    for row in rows:
        definition = registry.get(row.namespace, row.key)
        if definition is None or not definition.restart_required:
            continue
        if parse_iso_utc(row.updated_at) <= since:
            continue
        pending.append(
            PendingRestartSetting(
                namespace=definition.namespace,
                key=NotBlankStr(definition.key),
                description=definition.description,
                updated_at=NotBlankStr(row.updated_at),
            )
        )
    return tuple(sorted(pending, key=lambda p: (p.namespace, p.key)))
