"""Integration tests for ``SettingsRepository.set_many`` on both backends.

PR #1239 introduced single-key CAS on the settings repo.  This follow-up
adds a transactional ``set_many`` that writes multiple rows under CAS
in one shot, so mutations like ``delete_department`` can pin several
keys at once and avoid TOCTOU races.

The tests are duplicated across SQLite and Postgres rather than
parameterised with ``request.getfixturevalue``, because the latter
clashes with ``pytest-asyncio``'s runner when the underlying fixture
is async.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.settings_protocol import SettingRow, SettingsRepository
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend


def _iso(minute: int) -> str:
    return datetime(2026, 4, 11, 12, minute, 0, tzinfo=UTC).isoformat()


def _row(ns: str, key: str, value: str, ts: str) -> SettingRow:
    return SettingRow(
        namespace=NotBlankStr(ns),
        key=NotBlankStr(key),
        value=value,
        updated_at=ts,
    )


async def _run_all_success(repo: SettingsRepository) -> None:
    ok = await repo.set_many(
        [
            _row("company", "departments", "[]", _iso(0)),
            _row("company", "agents", "[]", _iso(0)),
            _row("company", "company_name", "Acme", _iso(0)),
        ],
    )
    assert ok is True
    for key in ("departments", "agents", "company_name"):
        row = await repo.get((NotBlankStr("company"), NotBlankStr(key)))
        assert row is not None
        assert row.value in ("[]", "Acme")


async def _run_cas_conflict_rolls_back(repo: SettingsRepository) -> None:
    await repo.set_if_unchanged(
        _row("company", "departments", "[]", _iso(0)),
        expected_updated_at="",
    )
    await repo.set_if_unchanged(
        _row("company", "agents", "[]", _iso(0)),
        expected_updated_at="",
    )
    stale_dept_row = await repo.get(
        (NotBlankStr("company"), NotBlankStr("departments")),
    )
    live_agents_row = await repo.get(
        (NotBlankStr("company"), NotBlankStr("agents")),
    )
    assert stale_dept_row is not None
    assert live_agents_row is not None
    stale_dept_version = stale_dept_row.updated_at
    live_agents_version = live_agents_row.updated_at
    # Bump departments out from under the upcoming set_many so the
    # CAS check fails when the batch runs.
    await repo.set_if_unchanged(
        _row("company", "departments", '["bumped"]', _iso(5)),
        expected_updated_at=stale_dept_version,
    )

    ok = await repo.set_many(
        [
            _row("company", "departments", '["new"]', _iso(10)),
            _row("company", "agents", '["new-agent"]', _iso(10)),
        ],
        expected_updated_at_map={
            (NotBlankStr("company"), NotBlankStr("departments")): stale_dept_version,
            (NotBlankStr("company"), NotBlankStr("agents")): live_agents_version,
        },
    )
    assert ok is False
    dept_row = await repo.get((NotBlankStr("company"), NotBlankStr("departments")))
    agents_row = await repo.get((NotBlankStr("company"), NotBlankStr("agents")))
    assert dept_row is not None
    assert agents_row is not None
    assert dept_row.value == '["bumped"]'
    assert agents_row.value == "[]"


async def _run_first_write_sentinel(repo: SettingsRepository) -> None:
    ok = await repo.set_many(
        [_row("company", "departments", "[]", _iso(0))],
        expected_updated_at_map={
            (NotBlankStr("company"), NotBlankStr("departments")): "",
        },
    )
    assert ok is True
    row = await repo.get((NotBlankStr("company"), NotBlankStr("departments")))
    assert row is not None
    assert row.value == "[]"


async def _run_no_cas_upserts(repo: SettingsRepository) -> None:
    await repo.set_if_unchanged(
        _row("company", "company_name", "Acme", _iso(0)),
        expected_updated_at="",
    )
    ok = await repo.set_many(
        [_row("company", "company_name", "Zeta", _iso(10))],
    )
    assert ok is True
    row = await repo.get((NotBlankStr("company"), NotBlankStr("company_name")))
    assert row is not None
    assert row.value == "Zeta"


async def _run_empty_noop(repo: SettingsRepository) -> None:
    ok = await repo.set_many([])
    assert ok is True


async def _run_mixed(repo: SettingsRepository) -> None:
    await repo.set_if_unchanged(
        _row("company", "departments", "[]", _iso(0)),
        expected_updated_at="",
    )
    live_dept_row = await repo.get(
        (NotBlankStr("company"), NotBlankStr("departments")),
    )
    assert live_dept_row is not None
    live_dept_version = live_dept_row.updated_at

    ok = await repo.set_many(
        [
            _row("company", "departments", '["a"]', _iso(10)),
            _row("company", "autonomy_level", "L3", _iso(10)),
        ],
        expected_updated_at_map={
            (NotBlankStr("company"), NotBlankStr("departments")): live_dept_version,
        },
    )
    assert ok is True
    dept_row = await repo.get((NotBlankStr("company"), NotBlankStr("departments")))
    auton_row = await repo.get((NotBlankStr("company"), NotBlankStr("autonomy_level")))
    assert dept_row is not None
    assert dept_row.value == '["a"]'
    assert auton_row is not None
    assert auton_row.value == "L3"


@pytest.mark.integration
class TestSetManySqlite:
    async def test_all_success(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _run_all_success(on_disk_backend.settings)

    async def test_cas_conflict_rolls_back(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _run_cas_conflict_rolls_back(on_disk_backend.settings)

    async def test_first_write_sentinel(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _run_first_write_sentinel(on_disk_backend.settings)

    async def test_no_cas_upserts(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _run_no_cas_upserts(on_disk_backend.settings)

    async def test_empty_noop(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _run_empty_noop(on_disk_backend.settings)

    async def test_mixed(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _run_mixed(on_disk_backend.settings)


@pytest.mark.integration
class TestSetManyPostgres:
    async def test_all_success(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _run_all_success(postgres_backend.settings)

    async def test_cas_conflict_rolls_back(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _run_cas_conflict_rolls_back(postgres_backend.settings)

    async def test_first_write_sentinel(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _run_first_write_sentinel(postgres_backend.settings)

    async def test_no_cas_upserts(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _run_no_cas_upserts(postgres_backend.settings)

    async def test_empty_noop(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _run_empty_noop(postgres_backend.settings)

    async def test_mixed(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _run_mixed(postgres_backend.settings)
