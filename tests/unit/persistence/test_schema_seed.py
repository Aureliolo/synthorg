"""Guards for the SQLite schema seed and the Windows pg-template loop pin.

These cover two regressions that would otherwise only surface far
downstream (a Postgres-only Windows crash, or a truncated schema seed):

* ``run_pg_template_build`` must forward ``loop_factory=SelectorEventLoop``
  on Windows. A bare ``asyncio.run`` there builds ``ProactorEventLoop``,
  which psycopg 3's async mode cannot drive. The test pins ``sys.platform``
  so it catches a dropped ``loop_factory`` on any host (Linux CI included),
  not only when the suite happens to run on Windows.
* The declared ``schema.sql`` that ``tests/_shared/persistence.py`` feeds
  into its in-memory ``seen_claims`` double must build the full table set;
  a truncated schema would pass the drift gate (empty-vs-empty) yet break
  every consumer.
"""

import asyncio
import sqlite3
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path

import pytest

from tests._shared import postgres_template
from tests._shared.persistence import _SQLITE_SCHEMA_DDL
from tests._shared.postgres_proxy import PostgresContainerProxy

pytestmark = pytest.mark.unit

# A floor, not the exact count: the full schema defines dozens of tables,
# so any value comfortably above a handful guards against truncation
# without churning every time a table is added.
_MIN_EXPECTED_TABLES = 30

_LoopFactory = Callable[[], asyncio.AbstractEventLoop] | None


def _captured_loop_factory(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> _LoopFactory:
    """Run ``run_pg_template_build`` with a stubbed runner; return loop_factory."""
    captured: dict[str, _LoopFactory] = {}

    async def _fake_ensure(proxy: PostgresContainerProxy, shared_dir: Path) -> str:
        return postgres_template.TEMPLATE_DB_NAME

    def _fake_run(
        coro: Coroutine[object, object, str],
        *,
        loop_factory: _LoopFactory = None,
    ) -> str:
        captured["loop_factory"] = loop_factory
        coro.close()  # the coroutine is never awaited under the stub
        return postgres_template.TEMPLATE_DB_NAME

    monkeypatch.setattr(postgres_template, "ensure_pg_template", _fake_ensure)
    monkeypatch.setattr(asyncio, "run", _fake_run)
    monkeypatch.setattr(sys, "platform", platform)
    proxy = PostgresContainerProxy(
        host="unused", port=5432, username="u", password="p", dbname="d"
    )
    postgres_template.run_pg_template_build(proxy, Path("unused"))
    return captured["loop_factory"]


def test_run_pg_template_build_pins_selector_loop_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On win32 the build forwards ``loop_factory=SelectorEventLoop``."""
    assert _captured_loop_factory(monkeypatch, "win32") is asyncio.SelectorEventLoop


def test_run_pg_template_build_uses_default_loop_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off win32 ``loop_factory`` stays ``None`` (asyncio.run's default)."""
    assert _captured_loop_factory(monkeypatch, "linux") is None


def test_sqlite_schema_seed_builds_full_table_set() -> None:
    """The declared SQLite schema applies cleanly and yields the full table set.

    Catches a truncated ``schema.sql`` that would pass the drift gate
    (empty matches empty) but break the ``make_sqlite_seen_claims`` double
    and every other consumer of the seed.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_SQLITE_SCHEMA_DDL)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()

    assert "seen_claims" in tables
    assert len(tables) >= _MIN_EXPECTED_TABLES, (
        f"schema seed produced only {len(tables)} tables; schema.sql may be truncated"
    )
