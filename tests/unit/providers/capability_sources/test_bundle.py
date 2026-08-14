"""The shipped capability snapshot, and seeding an installation from it."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources.bundle import (
    BUNDLE_FILENAME,
    BUNDLED_FEED_URL,
    load_bundled_snapshot,
)
from synthorg.providers.capability_sources.ingest import CapabilityIngestService
from synthorg.providers.capability_sources.models import CapabilityScore
from synthorg.providers.capability_sources.registry import (
    EPOCH_LABEL,
    list_capability_sources,
)
from synthorg.providers.capability_sources.status import CapabilitySourceStatus
from tests._shared import FakeClock
from tests.unit.providers.capability_sources.conftest import SECOND_LABEL

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

#: Grading refuses to rank within a cohort smaller than this, so a bundled
#: axis below it seeds rows that can never grade anything. Asserting the
#: cohort rather than a row count keeps the check meaningful as the feed's
#: volume moves: what matters is whether a percentile means something, not
#: how many measurements happen to be behind it.
_MIN_GRADABLE_COHORT = 5


def _bundle_path() -> Path:
    import synthorg.providers.capability_sources as package

    return Path(str(package.__file__)).parent / BUNDLE_FILENAME


class _RecordingScores:
    def __init__(self) -> None:
        self.rows: list[CapabilityScore] = []

    async def save_many(self, entities: tuple[CapabilityScore, ...]) -> None:
        self.rows.extend(entities)


class _MemoryStatuses:
    def __init__(self, seeded: dict[str, CapabilitySourceStatus] | None = None) -> None:
        self.store: dict[str, CapabilitySourceStatus] = dict(seeded or {})

    async def get(self, entity_id: NotBlankStr) -> CapabilitySourceStatus | None:
        return self.store.get(str(entity_id))

    async def save(self, entity: CapabilitySourceStatus) -> None:
        self.store[str(entity.source_label)] = entity

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[CapabilitySourceStatus, ...]:
        del limit, offset
        return tuple(self.store[k] for k in sorted(self.store))


class _NeverFetches:
    async def fetch(self, url: str) -> bytes:
        msg = f"seeding must not fetch, but it asked for {url}"
        raise AssertionError(msg)


def _service(
    statuses: _MemoryStatuses | None = None,
) -> tuple[CapabilityIngestService, _RecordingScores, _MemoryStatuses]:
    scores = _RecordingScores()
    store = statuses if statuses is not None else _MemoryStatuses()
    return (
        CapabilityIngestService(
            fetcher=_NeverFetches(),
            scores=scores,
            statuses=store,
            clock=FakeClock(start=_NOW),
        ),
        scores,
        store,
    )


class TestShippedSnapshot:
    def test_the_snapshot_ships_with_the_package(self) -> None:
        assert _bundle_path().is_file()

    def test_it_covers_every_registered_source(self) -> None:
        """A source missing from the bundle grades nothing until it is fetched.

        That is the offline installation silently running on half the
        evidence, which is the thing shipping a bundle is meant to prevent.
        """
        snapshot = load_bundled_snapshot(ingested_at=_NOW)
        assert snapshot is not None
        assert set(snapshot.labels()) == {
            str(spec.label) for spec in list_capability_sources()
        }

    def test_it_is_not_a_partial_capture(self) -> None:
        document = json.loads(_bundle_path().read_text(encoding="utf-8"))
        assert document["partial"] is False

    def test_each_bundled_axis_carries_a_cohort_worth_ranking_in(self) -> None:
        """A rung is a rank, so an axis with three models grades nothing."""
        snapshot = load_bundled_snapshot(ingested_at=_NOW)
        assert snapshot is not None
        for label in snapshot.labels():
            per_axis: dict[str, set[str]] = {}
            for row in snapshot.scores_for(label):
                per_axis.setdefault(str(row.axis), set()).add(str(row.model_identifier))
            assert per_axis, label
            for axis, models in per_axis.items():
                assert len(models) >= _MIN_GRADABLE_COHORT, (label, axis, len(models))

    def test_bundled_evidence_ages_from_the_capture_not_the_boot(self) -> None:
        """A year-old snapshot must not read as fresh on a new install.

        ``as_of`` means "when the source last told us this", so for a
        bundled row it is when the RELEASE read the feed. Stamping the boot
        instead would reset the clock on every install, which is the one
        thing a floor must not do.
        """
        snapshot = load_bundled_snapshot(ingested_at=_NOW)
        assert snapshot is not None
        rows = [
            row for label in snapshot.labels() for row in snapshot.scores_for(label)
        ]
        assert rows
        assert all(row.as_of == snapshot.captured_at for row in rows)
        assert all(row.ingested_at == _NOW for row in rows)


class TestSeeding:
    async def test_seeding_writes_the_snapshot_without_fetching(self) -> None:
        service, scores, statuses = _service()

        snapshot = load_bundled_snapshot(ingested_at=_NOW)
        assert snapshot is not None

        expected = sum(len(snapshot.scores_for(label)) for label in snapshot.labels())

        seeded = await service.seed_from_bundle()

        assert {str(s.source_label) for s in seeded} == set(snapshot.labels())
        assert len(scores.rows) == expected > 0
        assert statuses.store[EPOCH_LABEL].feed_url == BUNDLED_FEED_URL

    async def test_a_source_with_a_history_is_left_alone(
        self, bundled_two_sources: tuple[str, str]
    ) -> None:
        """A months-old snapshot must never overwrite a live fetch."""
        del bundled_two_sources
        existing = CapabilitySourceStatus(
            source_label=NotBlankStr(EPOCH_LABEL),
            last_attempted_at=_NOW,
            last_succeeded_at=_NOW,
            rows_read=5,
            scores_written=5,
            feed_url="https://epoch.test/feed.csv",
        )
        service, scores, statuses = _service(_MemoryStatuses({EPOCH_LABEL: existing}))

        seeded = await service.seed_from_bundle()

        assert [str(s.source_label) for s in seeded] == [SECOND_LABEL]
        assert statuses.store[EPOCH_LABEL] == existing
        assert all(str(r.source_label) != EPOCH_LABEL for r in scores.rows)

    async def test_a_failed_fetch_still_counts_as_a_history(
        self, bundled_two_sources: tuple[str, str]
    ) -> None:
        """Seeding after a failure would hide the failure behind old rows."""
        del bundled_two_sources
        failed = CapabilitySourceStatus(
            source_label=NotBlankStr(EPOCH_LABEL),
            last_attempted_at=_NOW,
            last_succeeded_at=None,
            last_error="TimeoutError: upstream is not answering",
        )
        service, _, statuses = _service(_MemoryStatuses({EPOCH_LABEL: failed}))

        seeded = await service.seed_from_bundle()

        assert [str(s.source_label) for s in seeded] == [SECOND_LABEL]
        assert statuses.store[EPOCH_LABEL].last_error.startswith("TimeoutError")

    async def test_seeding_twice_writes_nothing_the_second_time(self) -> None:
        service, scores, _ = _service()
        await service.seed_from_bundle()
        first_count = len(scores.rows)

        seeded_again = await service.seed_from_bundle()

        assert seeded_again == ()
        assert len(scores.rows) == first_count

    async def test_a_seeded_source_reports_where_its_rows_came_from(self) -> None:
        service, _, _ = _service()
        await service.seed_from_bundle()

        reported = {str(s.source_label): s for s in await service.statuses()}

        assert reported[EPOCH_LABEL].feed_url == BUNDLED_FEED_URL
        assert reported[EPOCH_LABEL].is_healthy


def _document(rows: Sequence[object]) -> str:
    return json.dumps(
        {
            "captured_at": "2026-08-13T00:00:00+00:00",
            "partial": False,
            "sources": {"source-a": rows},
        },
    )


class TestCorruptSnapshot:
    def test_a_snapshot_that_is_not_json_degrades_rather_than_raising(self) -> None:
        """An installation with a broken bundle boots on the heuristic."""
        assert load_bundled_snapshot(ingested_at=_NOW, document="not json") is None

    def test_a_snapshot_missing_its_capture_date_is_refused(self) -> None:
        document = json.dumps({"sources": {}})
        assert load_bundled_snapshot(ingested_at=_NOW, document=document) is None

    def test_a_mis_shaped_row_is_dropped_and_the_rest_still_load(self) -> None:
        """One corrupt line must not cost an installation its whole snapshot."""
        snapshot = load_bundled_snapshot(
            ingested_at=_NOW,
            document=_document(
                [
                    ["model-y", "coding", 80.0],
                    ["model-z", "not-an-axis", 80.0],
                    ["model-w", "coding"],
                    ["model-v", "coding", 900.0],
                    ["model-u", "coding", 80.0, "a fourth field"],
                ],
            ),
        )

        assert snapshot is not None
        kept = snapshot.scores_for("source-a")
        assert [str(s.model_identifier) for s in kept] == ["model-y"]
