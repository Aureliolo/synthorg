"""Conformance tests for ``AlertRepository``.

Dual-backend parity: one assertion set runs against SQLite and Postgres
via the ``backend`` fixture. Covers append + newest-first query, the
filter spec (severity / alert_type / window), pagination offset,
``get_by_id`` resolution, and ``purge_before`` retention.
"""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import aiosqlite
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import Alert
from synthorg.meta.models import RuleSeverity
from synthorg.persistence.alert_protocol import AlertFilterSpec, AlertRepository
from synthorg.persistence.postgres.alert_repo import PostgresAlertRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.alert_repo import SQLiteAlertRepository

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> AlertRepository:
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteAlertRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresAlertRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _alert(  # noqa: PLR0913 -- test fixture builder, one kwarg per Alert field
    *,
    severity: RuleSeverity = RuleSeverity.WARNING,
    alert_type: str = "inflection",
    description: str = "Quality dropped sharply",
    affected_domains: tuple[str, ...] = ("performance",),
    signal_context: dict[str, object] | None = None,
    recommended_action: str | None = None,
    emitted_at: datetime = _NOW,
) -> Alert:
    return Alert(
        severity=severity,
        alert_type=alert_type,  # type: ignore[arg-type]
        description=NotBlankStr(description),
        affected_domains=tuple(NotBlankStr(d) for d in affected_domains),
        signal_context=signal_context or {"metric": "quality", "old_value": 8.0},
        recommended_action=(
            NotBlankStr(recommended_action) if recommended_action else None
        ),
        emitted_at=emitted_at,
    )


class TestAlertAppendQuery:
    async def test_append_round_trip_newest_first(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        alerts = [
            _alert(description=f"alert-{i}", emitted_at=_NOW + timedelta(seconds=i))
            for i in range(3)
        ]
        for alert in alerts:
            await repo.append(alert)
        items = await repo.query(AlertFilterSpec())
        assert [str(a.description) for a in items] == ["alert-2", "alert-1", "alert-0"]
        assert items[0].emitted_at == _NOW + timedelta(seconds=2)
        assert items[0].severity == RuleSeverity.WARNING
        assert items[0].affected_domains == ("performance",)
        assert items[0].signal_context == {"metric": "quality", "old_value": 8.0}
        assert items[0].recommended_action is None

    async def test_round_trips_recommended_action_and_multiple_domains(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        alert = _alert(
            affected_domains=("performance", "budget"),
            recommended_action="Review recent deploys",
        )
        await repo.append(alert)
        items = await repo.query(AlertFilterSpec())
        assert items[0].affected_domains == ("performance", "budget")
        assert items[0].recommended_action == "Review recent deploys"

    async def test_filter_by_severity_and_alert_type(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.append(_alert(severity=RuleSeverity.INFO, alert_type="threshold"))
        await repo.append(
            _alert(severity=RuleSeverity.CRITICAL, alert_type="inflection")
        )
        await repo.append(_alert(severity=RuleSeverity.WARNING, alert_type="trend"))

        by_severity = await repo.query(AlertFilterSpec(severity=RuleSeverity.CRITICAL))
        assert len(by_severity) == 1
        assert by_severity[0].alert_type == "inflection"

        by_type = await repo.query(AlertFilterSpec(alert_type="threshold"))
        assert len(by_type) == 1
        assert by_type[0].severity == RuleSeverity.INFO

    async def test_pagination_offset(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for index in range(5):
            await repo.append(
                _alert(
                    description=f"alert-{index}",
                    emitted_at=_NOW + timedelta(seconds=index),
                )
            )
        first = await repo.query(AlertFilterSpec(), limit=2, offset=0)
        second = await repo.query(AlertFilterSpec(), limit=2, offset=2)
        assert [str(a.description) for a in first] == ["alert-4", "alert-3"]
        assert [str(a.description) for a in second] == ["alert-2", "alert-1"]

    async def test_window_filter(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        for index in range(4):
            await repo.append(_alert(emitted_at=_NOW + timedelta(hours=index)))
        window = await repo.query(
            AlertFilterSpec(
                since=_NOW + timedelta(hours=1),
                until=_NOW + timedelta(hours=3),
            )
        )
        assert len(window) == 2


class TestAlertGetById:
    async def test_get_by_id_resolves_appended_alert(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        alert = _alert(description="a specific alert")
        await repo.append(alert)
        resolved = await repo.get_by_id(alert.id)
        assert resolved is not None
        assert resolved.id == alert.id
        assert resolved.description == "a specific alert"

    async def test_get_by_id_returns_none_for_unknown_id(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get_by_id(uuid4()) is None


class TestAlertPurge:
    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.append(_alert(emitted_at=_NOW))
        await repo.append(_alert(emitted_at=_NOW + timedelta(days=2)))

        removed = await repo.purge_before(_NOW + timedelta(days=1))
        assert removed == 1
        remaining = await repo.query(AlertFilterSpec())
        assert len(remaining) == 1
        assert remaining[0].emitted_at == _NOW + timedelta(days=2)
