"""Conformance tests for ``CapabilitySourceStatusRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture in
``tests/conformance/persistence/conftest.py``. The repo is reached through
``backend.capability_source_statuses``, the same accessor the ingest path
uses.

Covers:

* CRUD round-trip keyed by source label (save / get / list / delete).
* ``save`` upsert semantics: a second attempt replaces the first row.
* A failed attempt persists with its reason while the earlier success
  timestamp survives, which is what tells an operator the evidence still
  grading is old rather than absent.
* Null timestamps round-trip, so a source that has never been attempted is
  distinguishable from one attempted at the epoch.
* ``list_items`` ordering (label ASC) + pagination.
* Invalid pagination args raise :class:`QueryError`.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.capability_source_status_protocol import (
    CapabilitySourceStatusRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.capability_sources.status import CapabilitySourceStatus

pytestmark = pytest.mark.integration

_ATTEMPTED = datetime(2026, 5, 21, 9, 30, tzinfo=UTC)
_SUCCEEDED = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> CapabilitySourceStatusRepository:
    """Return the status repository *backend* exposes."""
    return backend.capability_source_statuses


def _status(
    label: str = "source-a",
    *,
    last_attempted_at: datetime | None = _ATTEMPTED,
    last_succeeded_at: datetime | None = _SUCCEEDED,
    last_error: str = "",
    rows_read: int = 100,
    rows_skipped: int = 4,
    scores_written: int = 96,
    feed_url: str = "https://example.test/feed.csv",
) -> CapabilitySourceStatus:
    return CapabilitySourceStatus(
        source_label=NotBlankStr(label),
        last_attempted_at=last_attempted_at,
        last_succeeded_at=last_succeeded_at,
        last_error=last_error,
        rows_read=rows_read,
        rows_skipped=rows_skipped,
        scores_written=scores_written,
        feed_url=feed_url,
    )


class TestCrud:
    async def test_round_trip(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_status())

        stored = await repo.get(NotBlankStr("source-a"))

        assert stored is not None
        assert stored.last_attempted_at == _ATTEMPTED
        assert stored.last_succeeded_at == _SUCCEEDED
        assert stored.rows_read == 100
        assert stored.rows_skipped == 4
        assert stored.scores_written == 96
        assert stored.feed_url == "https://example.test/feed.csv"

    async def test_get_absent_returns_none(self, backend: PersistenceBackend) -> None:
        assert await _repo(backend).get(NotBlankStr("never-run")) is None

    async def test_save_upserts(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_status(rows_read=10))
        await repo.save(_status(rows_read=20))

        stored = await repo.get(NotBlankStr("source-a"))

        assert stored is not None
        assert stored.rows_read == 20
        assert len(await repo.list_items()) == 1

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_status())

        assert await repo.delete(NotBlankStr("source-a")) is True
        assert await repo.delete(NotBlankStr("source-a")) is False
        assert await repo.get(NotBlankStr("source-a")) is None


class TestFailureRecord:
    async def test_a_failure_keeps_the_earlier_success(
        self, backend: PersistenceBackend
    ) -> None:
        """Stale evidence beats none, and the record says which it is."""
        repo = _repo(backend)
        await repo.save(_status())
        await repo.save(
            _status(
                last_attempted_at=datetime(2026, 6, 1, tzinfo=UTC),
                last_succeeded_at=_SUCCEEDED,
                last_error="TimeoutError: upstream is not answering",
            ),
        )

        stored = await repo.get(NotBlankStr("source-a"))

        assert stored is not None
        assert stored.last_error.startswith("TimeoutError")
        assert stored.last_succeeded_at == _SUCCEEDED
        assert not stored.is_healthy

    async def test_a_source_never_attempted_round_trips_as_null(
        self, backend: PersistenceBackend
    ) -> None:
        """Never-run must not be storable only as some sentinel date."""
        repo = _repo(backend)
        await repo.save(
            _status(
                label="never-run",
                last_attempted_at=None,
                last_succeeded_at=None,
                rows_read=0,
                rows_skipped=0,
                scores_written=0,
                feed_url="",
            ),
        )

        stored = await repo.get(NotBlankStr("never-run"))

        assert stored is not None
        assert stored.last_attempted_at is None
        assert stored.last_succeeded_at is None
        assert not stored.is_healthy


class TestListing:
    async def test_ordered_and_paginated(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_status(label="source-z"))
        await repo.save(_status(label="source-a"))
        await repo.save(_status(label="source-m"))

        labels = [str(s.source_label) for s in await repo.list_items()]
        assert labels == sorted(labels)

        page = await repo.list_items(limit=1, offset=1)
        assert len(page) == 1
        assert str(page[0].source_label) == labels[1]

    async def test_invalid_pagination_rejected(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        with pytest.raises(QueryError):
            await repo.list_items(limit=0)
        with pytest.raises(QueryError):
            await repo.list_items(offset=-1)
