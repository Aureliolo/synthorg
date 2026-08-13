# module-kind: adapter
"""Parser for the Epoch AI benchmark CSV.

The feed is one row per ``(model, benchmark)`` measurement, with the header
``model_id, benchmark_id, performance, benchmark, benchmark_release_date,
optimized, model, model_version, Model, model_group, Model aggregation,
Model Aggregation Date, date, source``.

Four of those columns carry everything this layer needs, and the parser
requires exactly those four so a feed reshuffle fails loudly instead of
producing plausible nonsense:

``model_version``
    The vendor's own model id (``amazon.nova-pro-v1:0``), which is what a
    provider config also names. The human-facing ``model`` column is
    deliberately ignored: a display name resolves to a configured pair
    only by guessing, and a matcher that guesses is how a wrong grade
    gets in.
``performance``
    The measurement, published on 0-1 and normalised here to 0-100.
``benchmark``
    Mapped onto an axis by :mod:`..axis_map`.
``date``
    When the measurement was taken. This becomes ``as_of``, so staleness
    reflects the measurement rather than the download.

Several benchmarks land on one axis, so their scores are averaged into a
single per-axis figure. The mean is the honest reduction: taking the best
would let one flattering benchmark speak for the axis, and taking the
worst would let one adversarial benchmark do the same.
"""

import csv
import io
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources.axis_map import axis_for_benchmark
from synthorg.providers.capability_sources.errors import CapabilitySourceParseError
from synthorg.providers.capability_sources.models import (
    SCORE_MAX,
    SCORE_MIN,
    CapabilityAxis,
    CapabilityScore,
)
from synthorg.providers.capability_sources.parsed_feed import ParsedFeed

_MODEL_COLUMN: Final[str] = "model_version"
_SCORE_COLUMN: Final[str] = "performance"
_BENCHMARK_COLUMN: Final[str] = "benchmark"
_DATE_COLUMN: Final[str] = "date"

_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    _MODEL_COLUMN,
    _SCORE_COLUMN,
    _BENCHMARK_COLUMN,
    _DATE_COLUMN,
)

#: The feed publishes ``performance`` as a 0-1 fraction; scores are stored
#: on 0-100 so two sources with different native ranges compare.
_FRACTION_TO_PERCENT: Final[float] = 100.0


def _parse_measured_date(raw: str) -> datetime | None:
    """Parse the feed's measurement date, or ``None`` when unusable.

    Returns:
        An aware UTC datetime, or ``None`` when *raw* is blank or not an
        ISO date.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _parse_score(raw: str) -> float | None:
    """Parse a 0-1 performance figure into a 0-100 score.

    Returns:
        The normalised score, or ``None`` when *raw* is not a number or
        falls outside the band the feed promises.
    """
    try:
        fraction = float(raw.strip())
    except ValueError:
        return None
    scaled = fraction * _FRACTION_TO_PERCENT
    if scaled < SCORE_MIN or scaled > SCORE_MAX:
        # A figure outside 0-1 means the column no longer holds what its
        # name says. Clamping would invent a plausible score from a value
        # we have just proved we do not understand.
        return None
    return scaled


def parse_epoch_csv(
    document: str,
    *,
    source_label: str,
    ingested_at: datetime,
) -> ParsedFeed:
    """Parse the Epoch AI benchmark CSV into per-axis scores.

    Args:
        document: The raw CSV text.
        source_label: Registry label to stamp on every score.
        ingested_at: When this installation read the feed.

    Returns:
        The per-``(model, axis)`` scores plus the read and skipped counts.

    Raises:
        CapabilitySourceParseError: When the document is not CSV, or has
            lost a column the parser needs. The caller marks this one
            source failed and leaves every other source untouched.
    """
    reader = csv.DictReader(io.StringIO(document))
    fieldnames = reader.fieldnames
    if not fieldnames:
        msg = "The Epoch CSV had no header row, so no column could be located."
        raise CapabilitySourceParseError(msg)
    missing = [c for c in _REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        msg = (
            f"The Epoch CSV is missing the column(s) {sorted(missing)} this "
            f"parser reads. Its shape has changed; the previously ingested "
            f"scores for this source are unchanged."
        )
        raise CapabilitySourceParseError(msg)

    totals: defaultdict[tuple[str, CapabilityAxis], list[float]] = defaultdict(list)
    measured: dict[tuple[str, CapabilityAxis], datetime] = {}
    rows_read = 0
    rows_skipped = 0

    for row in reader:
        rows_read += 1
        model_id = (row.get(_MODEL_COLUMN) or "").strip()
        score = _parse_score(row.get(_SCORE_COLUMN) or "")
        measured_at = _parse_measured_date(row.get(_DATE_COLUMN) or "")
        if not model_id or score is None or measured_at is None:
            rows_skipped += 1
            continue
        axis = axis_for_benchmark(row.get(_BENCHMARK_COLUMN) or "")
        key = (model_id, axis)
        totals[key].append(score)
        # The newest measurement in the group dates the group: an axis
        # averaged over an old and a fresh benchmark is only as stale as
        # its stalest input, but reporting the oldest would make every
        # actively-measured model look abandoned.
        previous = measured.get(key)
        if previous is None or measured_at > previous:
            measured[key] = measured_at

    scores = tuple(
        CapabilityScore(
            source_label=NotBlankStr(source_label),
            model_identifier=NotBlankStr(model_id),
            axis=axis,
            score=sum(values) / len(values),
            as_of=measured[(model_id, axis)],
            ingested_at=ingested_at,
        )
        for (model_id, axis), values in sorted(totals.items())
    )
    return ParsedFeed(
        scores=scores,
        rows_read=rows_read,
        rows_skipped=rows_skipped,
    )


__all__ = ["parse_epoch_csv"]
