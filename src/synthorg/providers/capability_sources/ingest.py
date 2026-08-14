# module-kind: service
"""Fetch, parse and persist one capability source at a time.

Every source is refreshed independently and records its own outcome. That
independence is the point: a source going quiet degrades the grading
rather than stopping it, and an operator choosing a model needs to see
which source is actually behind the number.

Three properties hold whatever happens:

* **A failure never clears a source's rows.** The last good evidence keeps
  grading, visibly ageing, because stale evidence beats none and the
  status record says which it is.
* **A refresh is age-gated.** Automatic refreshes only run for a source
  whose last attempt is older than the configured interval, so a busy
  dashboard does not re-fetch a leaderboard that moves once a day. The
  gate reads the last *attempt*, not the last success: gating on success
  would retry a broken feed on every single request.
* **A forced refresh ignores the gate and only the gate.** It still
  validates the URL, still parses, and still refuses to write a feed it
  could not read.
"""

import asyncio
from collections.abc import Iterable, Sequence
from datetime import timedelta
from typing import Final, Protocol, runtime_checkable

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import DEFAULT_PAGE_SIZE
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_CAPABILITY_SOURCE_FAILED,
    PROVIDER_CAPABILITY_SOURCE_INGESTED,
)
from synthorg.providers.capability_sources.bundle import (
    BUNDLED_FEED_URL,
    load_bundled_snapshot,
)
from synthorg.providers.capability_sources.config import (
    CapabilitySourceConfig,
    CapabilitySourceSetting,
)
from synthorg.providers.capability_sources.errors import (
    CapabilitySourceParseError,
    CapabilitySourceUnknownError,
)
from synthorg.providers.capability_sources.models import CapabilityScore
from synthorg.providers.capability_sources.parsers import parse_document
from synthorg.providers.capability_sources.registry import (
    CapabilitySourceSpec,
    get_capability_source,
    list_capability_sources,
)
from synthorg.providers.capability_sources.status import CapabilitySourceStatus

logger = get_logger(__name__)


#: Enough to hold every registered source's status in one read; the
#: registry is a handful of entries, not a growing table.
_STATUS_PAGE: Final[int] = 100


@runtime_checkable
class UrlGate(Protocol):
    """Decides whether a URL may be fetched."""

    def __call__(self, url: str) -> bool:
        """Return whether *url* is permitted."""
        ...


@runtime_checkable
class CapabilityFeedFetcher(Protocol):
    """Fetches a feed document over the network."""

    async def fetch(self, url: str) -> bytes:
        """Return the bytes at *url*."""
        ...


@runtime_checkable
class CapabilityScoreWriter(Protocol):
    """Persists a parsed feed's scores."""

    async def save_many(self, entities: tuple[CapabilityScore, ...]) -> None:
        """Persist *entities* all-or-nothing."""
        ...


@runtime_checkable
class CapabilitySourceStatusStore(Protocol):
    """Loads and persists per-source ingest status."""

    async def get(self, entity_id: NotBlankStr) -> CapabilitySourceStatus | None:
        """Return the status for one source, or ``None`` when never run."""
        ...

    async def save(self, entity: CapabilitySourceStatus) -> None:
        """Persist *entity*."""
        ...

    async def list_items(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[CapabilitySourceStatus, ...]:
        """Return every persisted status."""
        ...


class CapabilityIngestService:
    """Refreshes capability sources and records what each attempt did.

    Args:
        fetcher: Network fetcher for automatic refreshes.
        scores: Where parsed measurements are persisted.
        statuses: Where per-source outcomes are recorded.
        url_is_allowed: SSRF gate applied to every URL before it is
            fetched. Left unwired, no operator URL is accepted at all:
            fetching an unvalidated address is the failure this guards, so
            its absence closes the door rather than opening it.
        clock: Time source for attempt stamps and the age gate.
    """

    __slots__ = ("_clock", "_fetcher", "_scores", "_statuses", "_url_is_allowed")

    def __init__(
        self,
        *,
        fetcher: CapabilityFeedFetcher,
        scores: CapabilityScoreWriter,
        statuses: CapabilitySourceStatusStore,
        url_is_allowed: UrlGate | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._scores = scores
        self._statuses = statuses
        self._url_is_allowed = url_is_allowed
        self._clock = clock or SystemClock()

    async def refresh_due(
        self,
        config: CapabilitySourceConfig,
        *,
        interval: timedelta,
        force: bool = False,
    ) -> tuple[CapabilitySourceStatus, ...]:
        """Refresh every enabled source that is due (or all, when forced).

        Returns:
            The status of every enabled source afterwards, including the
            ones that were skipped as not yet due, in registry order.
        """
        settings = config.by_label()
        results: list[CapabilitySourceStatus | None] = []
        pending: list[tuple[int, CapabilitySourceSpec, str]] = []
        for spec in list_capability_sources():
            entry = settings.get(str(spec.label))
            if entry is not None and not entry.enabled:
                continue
            current = await self._statuses.get(NotBlankStr(str(spec.label)))
            if (
                not force
                and current is not None
                and not current.is_due(now=self._clock.now(), interval=interval)
            ):
                results.append(current)
                continue
            pending.append((len(results), spec, _feed_url(spec, entry)))
            results.append(None)
        # Every source is independent and ``_refresh_one`` turns any failure
        # into a status row, so one slow feed must not delay the rest: fetched
        # sequentially, a source that hangs to its deadline held up every
        # source after it in registry order.
        async with asyncio.TaskGroup() as group:
            tasks = [
                (slot, group.create_task(self._refresh_one(spec, url)))
                for slot, spec, url in pending
            ]
        for slot, task in tasks:
            results[slot] = task.result()
        return tuple(status for status in results if status is not None)

    async def refresh_source(
        self,
        label: str,
        config: CapabilitySourceConfig,
    ) -> CapabilitySourceStatus:
        """Refresh one named source now, ignoring the age gate.

        Returns:
            The status the attempt produced.

        Raises:
            CapabilitySourceUnknownError: When *label* names no registered
                source. Guessing at a near-miss would file evidence under
                a name the operator cannot find it by.
        """
        spec = _require_source(label)
        entry = config.by_label().get(label)
        return await self._refresh_one(spec, _feed_url(spec, entry))

    async def ingest_document(
        self,
        label: str,
        document: bytes,
    ) -> CapabilitySourceStatus:
        """Ingest an operator-supplied document for one source.

        Takes the same path as an automatic refresh minus the fetch, so an
        upload cannot land rows a refresh would have rejected.

        Returns:
            The status the ingest produced.

        Raises:
            CapabilitySourceUnknownError: When *label* names no registered
                source.
        """
        spec = _require_source(label)
        return await self._apply(spec, document, feed_url="operator upload")

    async def _refresh_one(
        self,
        spec: CapabilitySourceSpec,
        url: str,
    ) -> CapabilitySourceStatus:
        """Fetch and apply one source, recording the outcome either way.

        Returns:
            The status the attempt produced.
        """
        if url != str(spec.feed_url) and not self._url_allowed(url):
            return await self._record_failure(
                spec,
                url,
                reason=(
                    "The configured feed URL is not permitted by the network "
                    "allowlist, so it was not fetched."
                ),
            )
        try:
            document = await self._fetcher.fetch(url)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            return await self._record_failure(
                spec,
                url,
                reason=f"{type(exc).__name__}: {safe_error_description(exc)}",
            )
        return await self._apply(spec, document, feed_url=url)

    async def _apply(
        self,
        spec: CapabilitySourceSpec,
        document: bytes,
        *,
        feed_url: str,
    ) -> CapabilitySourceStatus:
        """Parse and persist one document, recording the outcome.

        Returns:
            The status the attempt produced.
        """
        now = self._clock.now()
        try:
            parsed = parse_document(
                str(spec.parser_key),
                document,
                source_label=str(spec.label),
                ingested_at=now,
            )
        except CapabilitySourceParseError as exc:
            return await self._record_failure(
                spec, feed_url, reason=safe_error_description(exc)
            )
        try:
            await self._scores.save_many(parsed.scores)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            return await self._record_failure(
                spec,
                feed_url,
                reason=f"{type(exc).__name__}: {safe_error_description(exc)}",
            )
        status = CapabilitySourceStatus(
            source_label=NotBlankStr(str(spec.label)),
            last_attempted_at=now,
            last_succeeded_at=now,
            last_error="",
            rows_read=parsed.rows_read,
            rows_skipped=parsed.rows_skipped,
            scores_written=len(parsed.scores),
            feed_url=feed_url,
        )
        await self._statuses.save(status)
        logger.info(
            PROVIDER_CAPABILITY_SOURCE_INGESTED,
            source_label=str(spec.label),
            rows_read=parsed.rows_read,
            rows_skipped=parsed.rows_skipped,
            scores_written=len(parsed.scores),
        )
        return status

    async def _record_failure(
        self,
        spec: CapabilitySourceSpec,
        feed_url: str,
        *,
        reason: str,
    ) -> CapabilitySourceStatus:
        """Stamp a failed attempt without disturbing the source's rows.

        Returns:
            The recorded status, carrying forward the last success so an
            operator can see how old the evidence still grading is.
        """
        previous = await self._statuses.get(NotBlankStr(str(spec.label)))
        status = CapabilitySourceStatus(
            source_label=NotBlankStr(str(spec.label)),
            last_attempted_at=self._clock.now(),
            last_succeeded_at=previous.last_succeeded_at if previous else None,
            last_error=reason,
            rows_read=previous.rows_read if previous else 0,
            rows_skipped=previous.rows_skipped if previous else 0,
            scores_written=previous.scores_written if previous else 0,
            feed_url=feed_url,
        )
        await self._statuses.save(status)
        logger.warning(
            PROVIDER_CAPABILITY_SOURCE_FAILED,
            source_label=str(spec.label),
            reason=reason,
            has_previous_evidence=status.last_succeeded_at is not None,
        )
        return status

    def _url_allowed(self, url: str) -> bool:
        """Whether the SSRF gate permits fetching *url*.

        Returns:
            ``False`` when no gate is wired, because an unvalidated
            address is exactly what the gate exists to refuse.
        """
        if self._url_is_allowed is None:
            return False
        return self._url_is_allowed(url)

    async def seed_from_bundle(self) -> tuple[CapabilitySourceStatus, ...]:
        """Seed sources that have never been fetched here from the release.

        Only a source with no attempt on record is seeded. One that has
        been fetched, successfully or not, already has its own answer, and
        overwriting it with a months-old snapshot would move an
        installation backwards.

        Returns:
            The status of every source this call seeded, empty when the
            snapshot is absent or every source already has a history.
        """
        now = self._clock.now()
        snapshot = load_bundled_snapshot(ingested_at=now)
        if snapshot is None:
            return ()
        seeded: list[CapabilitySourceStatus] = []
        for label in snapshot.labels():
            if await self._statuses.get(NotBlankStr(label)) is not None:
                continue
            rows = snapshot.scores_for(label)
            if not rows:
                continue
            try:
                await self._scores.save_many(rows)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- seeding runs from wiring, so a
                # repository fault must degrade one label to unseeded rather
                # than escape onto a startup path and abandon the rest
                reraise_critical(exc)
                logger.warning(
                    PROVIDER_CAPABILITY_SOURCE_FAILED,
                    source_label=label,
                    from_bundle=True,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            status = CapabilitySourceStatus(
                source_label=NotBlankStr(label),
                last_attempted_at=now,
                last_succeeded_at=now,
                last_error="",
                rows_read=len(rows),
                rows_skipped=0,
                scores_written=len(rows),
                feed_url=BUNDLED_FEED_URL,
            )
            await self._statuses.save(status)
            seeded.append(status)
            logger.info(
                PROVIDER_CAPABILITY_SOURCE_INGESTED,
                source_label=label,
                scores_written=len(rows),
                from_bundle=True,
                captured_at=snapshot.captured_at.isoformat(),
            )
        return tuple(seeded)

    async def statuses(self) -> tuple[CapabilitySourceStatus, ...]:
        """Return the recorded status of every source, registered or not.

        Returns:
            One entry per registered source, defaulting to a never-run
            status so a source that has never been fetched is reported as
            such rather than omitted.
        """
        recorded = {
            str(s.source_label): s
            for s in await self._statuses.list_items(limit=_STATUS_PAGE)
        }
        return tuple(
            recorded.get(
                str(spec.label),
                CapabilitySourceStatus(source_label=NotBlankStr(str(spec.label))),
            )
            for spec in list_capability_sources()
        )


def _feed_url(
    spec: CapabilitySourceSpec,
    entry: CapabilitySourceSetting | None,
) -> str:
    """Return the URL to fetch for one source.

    Returns:
        The operator's URL when they set one, else the registry default.
    """
    if entry is not None and entry.feed_url:
        return str(entry.feed_url)
    return str(spec.feed_url)


def _require_source(label: str) -> CapabilitySourceSpec:
    """Resolve *label* to its spec.

    Returns:
        The registered spec.

    Raises:
        CapabilitySourceUnknownError: When nothing is registered under
            *label*.
    """
    spec = get_capability_source(label)
    if spec is None:
        known = ", ".join(sorted(str(s.label) for s in list_capability_sources()))
        msg = f"No capability source named {label!r} is registered. Known: {known}."
        raise CapabilitySourceUnknownError(msg)
    return spec


def enabled_labels(config: CapabilitySourceConfig) -> tuple[str, ...]:
    """Return the labels contributing evidence under *config*.

    Returns:
        Every registered label the operator has not switched off.
    """
    settings = config.by_label()
    return tuple(
        str(spec.label)
        for spec in list_capability_sources()
        if (entry := settings.get(str(spec.label))) is None or entry.enabled
    )


def scores_for_enabled(
    scores: Iterable[CapabilityScore],
    enabled: Sequence[str],
) -> tuple[CapabilityScore, ...]:
    """Keep only the scores whose source is currently enabled.

    A disabled source's rows stay in the table rather than being deleted,
    so switching it back on restores its evidence without a re-fetch. That
    means the filter has to happen on read.

    Returns:
        The scores from enabled sources.
    """
    allowed = set(enabled)
    return tuple(s for s in scores if str(s.source_label) in allowed)


__all__ = [
    "CapabilityFeedFetcher",
    "CapabilityIngestService",
    "CapabilityScoreWriter",
    "CapabilitySourceStatusStore",
    "UrlGate",
    "enabled_labels",
    "scores_for_enabled",
]
