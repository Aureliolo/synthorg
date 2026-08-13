# module-kind: adapter
"""Parser for the LMArena leaderboard parquet.

LMArena publishes its board as a small parquet of already-aggregated rows,
one per ``(model_name, category)``, with the columns ``model_name,
organization, license, rating, rating_lower, rating_upper, variance,
vote_count, rank, category, leaderboard_publish_date``. The ``latest``
snapshot is the current board; the sibling ``full`` file is its history and
is deliberately not read, because a history would let a two-year-old
publication date win a group's ``as_of``.

``model_name`` carries the identifier a provider is actually called with
rather than a display name, which is the property that lets a score be
matched to a configured model without guessing.

Two decisions worth stating, because both could reasonably have gone the
other way:

* **Categories are an allowlist, not a fallback.** The board slices the
  same votes 29 ways, and most slices are by language or by audience
  rather than by task. Averaging every slice into an axis would weight
  multilingual ability nine times over and let an audience segment stand
  in for a skill, so only task-shaped boards contribute and everything
  else is counted as skipped. This is the opposite posture to a feed with
  few, task-shaped categories, where an unclassified newcomer should still
  contribute.
* **The rating band is fixed, not derived.** Ratings are normalised onto
  0-100 against a declared band rather than the feed's own minimum and
  maximum, because a min-max would re-grade every model on the day one
  unusually weak entrant landed at the bottom.
"""

import io
import math
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Final

import pyarrow.parquet as pq

from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources.errors import CapabilitySourceParseError
from synthorg.providers.capability_sources.models import (
    SCORE_MAX,
    SCORE_MIN,
    CapabilityAxis,
    CapabilityScore,
)
from synthorg.providers.capability_sources.parsed_feed import ParsedFeed

_MODEL_COLUMN: Final[str] = "model_name"
_RATING_COLUMN: Final[str] = "rating"
_CATEGORY_COLUMN: Final[str] = "category"
_PUBLISHED_COLUMN: Final[str] = "leaderboard_publish_date"
_VOTES_COLUMN: Final[str] = "vote_count"

_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    _MODEL_COLUMN,
    _RATING_COLUMN,
    _CATEGORY_COLUMN,
    _PUBLISHED_COLUMN,
    _VOTES_COLUMN,
)

#: Only task-shaped boards contribute, matched exactly. The webdev names
#: appear in a sibling file rather than the default feed, and are mapped so
#: that an operator who points this source at that file gets coding
#: evidence instead of an empty parse.
_AXIS_BY_CATEGORY: Final[Mapping[str, CapabilityAxis]] = MappingProxyType(
    {
        "coding": "coding",
        "webdev": "coding",
        "webdev-html": "coding",
        "webdev-react": "coding",
        "math": "reasoning",
        "industry_mathematical": "reasoning",
        "hard_prompts": "reasoning",
        "expert": "reasoning",
        "overall": "general",
        "instruction_following": "general",
        "creative_writing": "general",
        "longer_query": "general",
        "multi_turn": "general",
    },
)

#: The band ratings are read against. The floor is the value the rating
#: system anchors an average entrant at, and the ceiling sits above the
#: current frontier so the models the ladder most needs to tell apart do
#: not all flatten onto 100.
_RATING_FLOOR: Final[float] = 900.0
_RATING_CEILING: Final[float] = 1650.0

#: Below this many votes a rating is noise rather than a measurement, and
#: the board itself publishes a confidence interval wide enough to say so.
_MIN_VOTES: Final[float] = 100.0

_PERCENT: Final[float] = 100.0


def _read_columns(document: bytes) -> dict[str, list[object]]:
    """Read the parquet's required columns into plain Python lists.

    Returns:
        A column-name to values mapping.

    Raises:
        CapabilitySourceParseError: When the bytes are not parquet, or
            have lost a column the parser reads.
    """
    try:
        table = pq.read_table(io.BytesIO(document))
    except Exception as exc:
        # pyarrow signals a truncated file, a wrong magic number and an
        # unsupported encoding through several unrelated exception types,
        # so the shape of the failure is caught rather than enumerated.
        # It is re-raised as the one typed error the per-source ingest
        # path knows how to contain.
        msg = (
            "The LMArena document could not be read as parquet. The "
            "previously ingested scores for this source are unchanged."
        )
        raise CapabilitySourceParseError(msg) from exc
    missing = [c for c in _REQUIRED_COLUMNS if c not in table.column_names]
    if missing:
        msg = (
            f"The LMArena parquet is missing the column(s) {sorted(missing)} "
            f"this parser reads. Its shape has changed; the previously "
            f"ingested scores for this source are unchanged."
        )
        raise CapabilitySourceParseError(msg)
    return {c: table.column(c).to_pylist() for c in _REQUIRED_COLUMNS}


def _as_score(raw: object) -> float | None:
    """Normalise one rating onto the shared 0-100 scale.

    A rating outside the declared band is held at the band's edge rather
    than discarded: unlike a malformed value it is a real measurement, and
    what it says is "at or beyond this end of the scale".

    Returns:
        The normalised score, or ``None`` when *raw* is absent or is not a
        finite number.
    """
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        return None
    rating = float(raw)
    if not math.isfinite(rating):
        return None
    fraction = (rating - _RATING_FLOOR) / (_RATING_CEILING - _RATING_FLOOR)
    return min(SCORE_MAX, max(SCORE_MIN, fraction * _PERCENT))


def _has_enough_votes(raw: object) -> bool:
    """Report whether a row rests on enough votes to be a measurement.

    Returns:
        ``True`` when the vote count is a number at or above the floor.
    """
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        return False
    votes = float(raw)
    return math.isfinite(votes) and votes >= _MIN_VOTES


def _as_published(raw: object) -> datetime | None:
    """Convert one publication date into an aware UTC datetime.

    Returns:
        The date as UTC midnight, or ``None`` when it is absent or not an
        ISO date.
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=UTC)
    if not isinstance(raw, str):
        return None
    try:
        parsed = date.fromisoformat(raw.strip())
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def parse_lmarena_parquet(
    document: bytes,
    *,
    source_label: str,
    ingested_at: datetime,
) -> ParsedFeed:
    """Read LMArena board rows into per-axis scores.

    Args:
        document: The raw parquet bytes.
        source_label: Registry label to stamp on every score.
        ingested_at: When this installation read the feed.

    Returns:
        The per-``(model, axis)`` scores plus the read and skipped counts.

    Raises:
        CapabilitySourceParseError: When the document is not parquet, or
            has lost a column the parser needs. The caller marks this one
            source failed and leaves every other source untouched.
    """
    columns = _read_columns(document)
    models = columns[_MODEL_COLUMN]
    ratings = columns[_RATING_COLUMN]
    categories = columns[_CATEGORY_COLUMN]
    published = columns[_PUBLISHED_COLUMN]
    votes = columns[_VOTES_COLUMN]

    totals: defaultdict[tuple[str, CapabilityAxis], list[float]] = defaultdict(list)
    measured: dict[tuple[str, CapabilityAxis], datetime] = {}
    rows_read = len(models)
    rows_skipped = 0

    for model_raw, rating_raw, category_raw, published_raw, votes_raw in zip(
        models, ratings, categories, published, votes, strict=True
    ):
        model_id = str(model_raw).strip() if model_raw is not None else ""
        category = str(category_raw).strip().casefold() if category_raw else ""
        axis = _AXIS_BY_CATEGORY.get(category)
        score = _as_score(rating_raw)
        stamp = _as_published(published_raw)
        if (
            not model_id
            or axis is None
            or score is None
            or stamp is None
            or not _has_enough_votes(votes_raw)
        ):
            rows_skipped += 1
            continue
        key = (model_id, axis)
        totals[key].append(score)
        previous = measured.get(key)
        if previous is None or stamp > previous:
            measured[key] = stamp

    aggregated = tuple(
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
        scores=aggregated,
        rows_read=rows_read,
        rows_skipped=rows_skipped,
    )


__all__ = ["parse_lmarena_parquet"]
