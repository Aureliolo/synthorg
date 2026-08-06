"""The retired-inner-loop settings migration rewrites stored rows.

A settings value is validated on write and never on read, so a row naming
``plan_execute`` or ``hybrid`` outlives the loops themselves and would surface
in the dashboard as a loop that no longer exists.

The revision is exercised by seeding the row *before* it runs: the earlier
revisions are applied from a pruned copy of the revisions directory, the stale
value is written through the repository, and the full directory is then applied
so yoyo runs exactly the one revision left. The Postgres file is byte-identical
(asserted below) and ``check_schema_drift_revisions.py --backend postgres``
executes it against a real server, so the SQL is proven on both backends.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.migration_helpers import revisions_dir
from synthorg.persistence.migrations import migrate_apply
from synthorg.persistence.settings_protocol import SettingRow
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

pytestmark = pytest.mark.integration

_REVISION_STEM = "20260806000000_retire_plan_execute_and_hybrid_loops"
_ENGINE = NotBlankStr("engine")
_DEFAULT_LOOP = NotBlankStr("default_loop_type")
_OVERRIDES = NotBlankStr("loop_complexity_overrides")


def _revisions_without_the_retirement(dest: Path) -> Path:
    """Copy the SQLite revisions directory minus the revision under test."""
    shutil.copytree(str(revisions_dir("sqlite")), str(dest))
    (dest / f"{_REVISION_STEM}.sql").unlink()
    return dest


async def _seed_and_migrate(
    tmp_path: Path,
    rows: tuple[SettingRow, ...],
) -> SQLitePersistenceBackend:
    """Apply every earlier revision, write *rows*, then apply the last one.

    Returns:
        A connected backend whose settings have been through the revision.
    """
    db_path = tmp_path / "retired-loops.db"
    db_url = f"sqlite:///{db_path}"
    pruned = _revisions_without_the_retirement(tmp_path / "pruned-revisions")

    await migrate_apply(db_url, revisions_path=pruned, backend="sqlite")

    backend = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await backend.connect()
    for row in rows:
        await backend.settings.save(row)
    await backend.disconnect()

    await migrate_apply(db_url, backend="sqlite")

    migrated = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await migrated.connect()
    return migrated


def _row(key: NotBlankStr, value: str) -> SettingRow:
    """Build an engine-namespace settings row carrying *value*."""
    return SettingRow(
        namespace=_ENGINE,
        key=key,
        value=value,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    )


class TestRetiredLoopSettingsMigration:
    @pytest.mark.parametrize("retired", ["plan_execute", "hybrid"])
    async def test_a_stored_default_loop_type_is_rewritten_to_react(
        self,
        tmp_path: Path,
        retired: str,
    ) -> None:
        backend = await _seed_and_migrate(tmp_path, (_row(_DEFAULT_LOOP, retired),))
        try:
            stored = await backend.settings.get((_ENGINE, _DEFAULT_LOOP))
            assert stored is not None
            assert stored.value == "react"
        finally:
            await backend.disconnect()

    async def test_overrides_naming_retired_loops_are_rewritten(
        self,
        tmp_path: Path,
    ) -> None:
        backend = await _seed_and_migrate(
            tmp_path,
            (_row(_OVERRIDES, "medium:plan_execute,complex:hybrid"),),
        )
        try:
            stored = await backend.settings.get((_ENGINE, _OVERRIDES))
            assert stored is not None
            assert stored.value == "medium:react,complex:react"
        finally:
            await backend.disconnect()

    async def test_a_live_loop_name_is_left_alone(
        self,
        tmp_path: Path,
    ) -> None:
        """Only the retired names move, so a measured route survives."""
        backend = await _seed_and_migrate(
            tmp_path,
            (
                _row(_DEFAULT_LOOP, "openhands"),
                _row(_OVERRIDES, "epic:openhands,medium:hybrid"),
            ),
        )
        try:
            default = await backend.settings.get((_ENGINE, _DEFAULT_LOOP))
            overrides = await backend.settings.get((_ENGINE, _OVERRIDES))
            assert default is not None
            assert default.value == "openhands"
            assert overrides is not None
            assert overrides.value == "epic:openhands,medium:react"
        finally:
            await backend.disconnect()

    def test_both_backends_ship_the_same_statements(self) -> None:
        """Dual-backend parity: the rewrite must not diverge by backend."""
        sqlite_sql = (revisions_dir("sqlite") / f"{_REVISION_STEM}.sql").read_text(
            encoding="utf-8"
        )
        postgres_sql = (revisions_dir("postgres") / f"{_REVISION_STEM}.sql").read_text(
            encoding="utf-8"
        )
        assert sqlite_sql == postgres_sql
