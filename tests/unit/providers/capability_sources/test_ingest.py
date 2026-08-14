"""Refreshing capability sources: age gating, failure posture, isolation."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources.config import (
    CapabilitySourceConfig,
    CapabilitySourceSetting,
)
from synthorg.providers.capability_sources.errors import (
    CapabilitySourceUnknownError,
)
from synthorg.providers.capability_sources.ingest import (
    CapabilityIngestService,
    enabled_labels,
    scores_for_enabled,
)
from synthorg.providers.capability_sources.models import CapabilityScore
from synthorg.providers.capability_sources.registry import (
    EPOCH_LABEL,
    CapabilitySourceSpec,
)
from synthorg.providers.capability_sources.status import CapabilitySourceStatus
from tests._shared import FakeClock
from tests.unit.providers.capability_sources.conftest import SECOND_LABEL

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_WEEK = timedelta(days=7)

_EPOCH_CSV = (
    "model_id,benchmark_id,performance,benchmark,benchmark_release_date,"
    "optimized,model,model_version,Model,model_group,Model aggregation,"
    "Model Aggregation Date,date,source\n"
    "m1,b1,0.80,SWE-Bench,2025-01-31,False,Model Y,model-y,Model Y,"
    "Model Y,,,2026-01-15,a source\n"
)


class _ScriptedFetcher:
    """Returns a canned body per URL, or raises what it was told to."""

    def __init__(self, bodies: dict[str, bytes | Exception]) -> None:
        self._bodies = bodies
        self.calls: list[str] = []

    async def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        body = self._bodies.get(url)
        if isinstance(body, Exception):
            raise body
        if body is None:
            msg = f"nothing scripted for {url}"
            raise AssertionError(msg)
        return body


class _MemoryScores:
    def __init__(self) -> None:
        self.rows: list[CapabilityScore] = []

    async def save_many(self, entities: tuple[CapabilityScore, ...]) -> None:
        self.rows.extend(entities)


class _MemoryStatuses:
    def __init__(self) -> None:
        self.store: dict[str, CapabilitySourceStatus] = {}

    async def get(self, entity_id: NotBlankStr) -> CapabilitySourceStatus | None:
        return self.store.get(str(entity_id))

    async def save(self, entity: CapabilitySourceStatus) -> None:
        self.store[str(entity.source_label)] = entity

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[CapabilitySourceStatus, ...]:
        del limit, offset
        return tuple(self.store[k] for k in sorted(self.store))


def _service(
    bodies: dict[str, bytes | Exception],
    *,
    now: datetime = _NOW,
    allow_urls: bool = True,
    statuses: _MemoryStatuses | None = None,
) -> tuple[CapabilityIngestService, _ScriptedFetcher, _MemoryScores, _MemoryStatuses]:
    fetcher = _ScriptedFetcher(bodies)
    scores = _MemoryScores()
    store = statuses if statuses is not None else _MemoryStatuses()
    service = CapabilityIngestService(
        fetcher=fetcher,
        scores=scores,
        statuses=store,
        url_is_allowed=(lambda _url: allow_urls),
        clock=FakeClock(start=now),
    )
    return service, fetcher, scores, store


def _urls_for(specs: tuple[CapabilitySourceSpec, ...]) -> dict[str, bytes | Exception]:
    return {str(spec.feed_url): _EPOCH_CSV.encode() for spec in specs}


def _url_of(specs: tuple[CapabilitySourceSpec, ...], label: str) -> str:
    return next(str(spec.feed_url) for spec in specs if str(spec.label) == label)


async def _refresh_again(
    now: datetime,
    statuses: _MemoryStatuses,
    bodies: dict[str, bytes | Exception],
    *,
    force: bool = False,
) -> tuple[CapabilityIngestService, _ScriptedFetcher, _MemoryScores, _MemoryStatuses]:
    """Run a second refresh against the status a first one left behind."""
    parts = _service(bodies, now=now, statuses=statuses)
    await parts[0].refresh_due(CapabilitySourceConfig(), interval=_WEEK, force=force)
    return parts


class TestAgeGate:
    async def test_a_source_never_fetched_is_due(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        service, fetcher, _, _ = _service(_urls_for(two_sources))
        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)
        assert len(fetcher.calls) == len(two_sources)

    async def test_a_source_refreshed_yesterday_is_left_alone(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        """A leaderboard that moves once a day is not re-fetched per request."""
        bodies = _urls_for(two_sources)
        service, _, _, statuses = _service(bodies)
        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)

        later = _NOW + timedelta(days=1)
        _, fetcher_later, _, _ = await _refresh_again(later, statuses, bodies)

        assert fetcher_later.calls == []

    async def test_a_source_older_than_the_interval_refreshes(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        bodies = _urls_for(two_sources)
        service, _, _, statuses = _service(bodies)
        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)

        later = _NOW + timedelta(days=8)
        _, fetcher_later, _, _ = await _refresh_again(later, statuses, bodies)

        assert len(fetcher_later.calls) == len(two_sources)

    async def test_force_ignores_the_gate(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        bodies = _urls_for(two_sources)
        service, _, _, statuses = _service(bodies)
        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)

        _, fetcher_again, _, _ = await _refresh_again(
            _NOW, statuses, bodies, force=True
        )

        assert len(fetcher_again.calls) == len(two_sources)

    async def test_the_gate_reads_the_attempt_not_the_success(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        """A broken feed retries on cadence, not on every single request.

        Gating on the last success would re-fetch a dead URL continuously
        for as long as it stayed dead.
        """
        bodies = _urls_for(two_sources)
        for url in bodies:
            bodies[url] = TimeoutError("upstream is not answering")
        service, fetcher, _, statuses = _service(bodies)
        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)
        assert len(fetcher.calls) == len(two_sources)

        _, fetcher_again, _, _ = await _refresh_again(_NOW, statuses, bodies)
        assert fetcher_again.calls == []


class TestFailurePosture:
    async def test_a_failed_fetch_is_recorded_not_raised(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        bodies = _urls_for(two_sources)
        bodies[_url_of(two_sources, EPOCH_LABEL)] = TimeoutError("not answering")
        service, _, _, statuses = _service(bodies)

        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)

        failed = statuses.store[EPOCH_LABEL]
        assert not failed.is_healthy
        assert "TimeoutError" in failed.last_error

    async def test_one_source_failing_leaves_the_other_working(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        """The refresh loop contains a failure to the source that raised it."""
        bodies = _urls_for(two_sources)
        bodies[_url_of(two_sources, EPOCH_LABEL)] = TimeoutError("not answering")
        service, _, scores, statuses = _service(bodies)

        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)

        assert not statuses.store[EPOCH_LABEL].is_healthy
        assert statuses.store[SECOND_LABEL].is_healthy
        assert [str(s.source_label) for s in scores.rows] == [SECOND_LABEL]

    async def test_a_failure_keeps_the_last_success_visible(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        """The evidence still grading is old, and the operator can see how old."""
        service, _, _, statuses = _service(_urls_for(two_sources))
        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)
        first_success = statuses.store[EPOCH_LABEL].last_succeeded_at

        bodies = _urls_for(two_sources)
        bodies[_url_of(two_sources, EPOCH_LABEL)] = TimeoutError("still not answering")
        later = _NOW + timedelta(days=30)
        service_later, _, _, statuses_later = _service(bodies, now=later)
        statuses_later.store.update(statuses.store)
        await service_later.refresh_due(
            CapabilitySourceConfig(), interval=_WEEK, force=True
        )

        failed = statuses_later.store[EPOCH_LABEL]
        assert failed.last_succeeded_at == first_success
        assert failed.last_attempted_at == later

    async def test_an_unreadable_document_writes_no_scores(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        bodies = _urls_for(two_sources)
        bodies[_url_of(two_sources, EPOCH_LABEL)] = b"<html>404</html>"
        service, _, scores, statuses = _service(bodies)

        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)

        assert not statuses.store[EPOCH_LABEL].is_healthy
        assert all(str(s.source_label) != EPOCH_LABEL for s in scores.rows)


class TestOperatorUrls:
    async def test_a_url_outside_the_allowlist_is_not_fetched(self) -> None:
        service, fetcher, _, statuses = _service(
            {"https://elsewhere.example/feed.csv": _EPOCH_CSV.encode()},
            allow_urls=False,
        )
        config = CapabilitySourceConfig(
            sources=(
                CapabilitySourceSetting(
                    label=NotBlankStr(EPOCH_LABEL),
                    feed_url="https://elsewhere.example/feed.csv",
                ),
            ),
        )

        await service.refresh_source(EPOCH_LABEL, config)

        assert fetcher.calls == []
        assert "allowlist" in statuses.store[EPOCH_LABEL].last_error

    async def test_the_registry_url_needs_no_allowlist_entry(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        """A shipped default is reviewed here, not at the operator's firewall."""
        service, fetcher, _, statuses = _service(
            _urls_for(two_sources), allow_urls=False
        )

        await service.refresh_source(EPOCH_LABEL, CapabilitySourceConfig())

        assert len(fetcher.calls) == 1
        assert statuses.store[EPOCH_LABEL].is_healthy

    async def test_an_allowed_operator_url_is_fetched(self) -> None:
        service, fetcher, _, statuses = _service(
            {"https://mirror.example/feed.csv": _EPOCH_CSV.encode()},
        )
        config = CapabilitySourceConfig(
            sources=(
                CapabilitySourceSetting(
                    label=NotBlankStr(EPOCH_LABEL),
                    feed_url="https://mirror.example/feed.csv",
                ),
            ),
        )

        await service.refresh_source(EPOCH_LABEL, config)

        assert fetcher.calls == ["https://mirror.example/feed.csv"]
        assert statuses.store[EPOCH_LABEL].feed_url == "https://mirror.example/feed.csv"


class TestUpload:
    async def test_an_operator_document_takes_the_same_path(self) -> None:
        service, fetcher, scores, _ = _service({})

        status = await service.ingest_document(EPOCH_LABEL, _EPOCH_CSV.encode())

        assert fetcher.calls == []
        assert status.is_healthy
        assert status.scores_written == len(scores.rows) > 0

    async def test_an_unreadable_upload_is_refused_not_stored(self) -> None:
        service, _, scores, _ = _service({})

        status = await service.ingest_document(EPOCH_LABEL, b"<html>nope</html>")

        assert not status.is_healthy
        assert scores.rows == []

    async def test_an_unknown_source_is_rejected_rather_than_guessed(self) -> None:
        service, _, _, _ = _service({})

        with pytest.raises(CapabilitySourceUnknownError, match="epoch-a1"):
            await service.ingest_document("epoch-a1", _EPOCH_CSV.encode())


class TestDisabling:
    async def test_a_disabled_source_is_not_fetched(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        service, fetcher, _, _ = _service(_urls_for(two_sources))
        config = CapabilitySourceConfig(
            sources=(
                CapabilitySourceSetting(label=NotBlankStr(EPOCH_LABEL), enabled=False),
            ),
        )

        await service.refresh_due(config, interval=_WEEK)

        assert _url_of(two_sources, EPOCH_LABEL) not in fetcher.calls

    def test_disabling_narrows_the_enabled_set(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        config = CapabilitySourceConfig(
            sources=(
                CapabilitySourceSetting(label=NotBlankStr(EPOCH_LABEL), enabled=False),
            ),
        )
        remaining = tuple(
            str(spec.label) for spec in two_sources if str(spec.label) != EPOCH_LABEL
        )
        assert enabled_labels(config) == remaining == (SECOND_LABEL,)

    def test_an_absent_config_enables_everything(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        """The defect this grading corrects is not opt-in."""
        assert set(enabled_labels(CapabilitySourceConfig())) == {
            str(spec.label) for spec in two_sources
        }

    def test_a_disabled_source_stops_contributing_without_losing_its_rows(
        self,
    ) -> None:
        """Re-enabling must restore evidence without a re-fetch."""
        rows = (
            CapabilityScore(
                source_label=NotBlankStr(EPOCH_LABEL),
                model_identifier=NotBlankStr("model-y"),
                axis="general",
                score=80.0,
                as_of=_NOW,
                ingested_at=_NOW,
            ),
            CapabilityScore(
                source_label=NotBlankStr(SECOND_LABEL),
                model_identifier=NotBlankStr("model-y"),
                axis="general",
                score=60.0,
                as_of=_NOW,
                ingested_at=_NOW,
            ),
        )
        kept = scores_for_enabled(rows, [SECOND_LABEL])
        assert [str(s.source_label) for s in kept] == [SECOND_LABEL]


class TestStatusReporting:
    async def test_a_source_never_run_still_appears(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        """Omitting it would read as "no problem" rather than "never tried"."""
        service, _, _, _ = _service({})

        reported = await service.statuses()

        assert {str(s.source_label) for s in reported} == {
            str(spec.label) for spec in two_sources
        }
        assert all(not s.is_healthy for s in reported)

    async def test_counts_survive_for_the_dashboard(
        self, two_sources: tuple[CapabilitySourceSpec, ...]
    ) -> None:
        service, _, _, _ = _service(_urls_for(two_sources))
        await service.refresh_due(CapabilitySourceConfig(), interval=_WEEK)

        reported = {str(s.source_label): s for s in await service.statuses()}

        assert reported[EPOCH_LABEL].rows_read > 0
        assert reported[EPOCH_LABEL].scores_written > 0
