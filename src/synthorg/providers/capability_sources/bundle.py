# module-kind: code
"""The capability snapshot that ships with the release.

An installation with no outbound network, or one whose first refresh has
not run yet, would otherwise grade every model on the size-and-price
heuristic: the exact proxy this whole layer exists to replace. So a
snapshot of both feeds is committed and seeded on first use.

The snapshot is a floor, never a ceiling. It seeds a source that has never
been fetched here and nothing else: once a live refresh lands, its rows
replace the bundled ones by the same upsert any refresh uses, and a source
that already has rows is left alone.

Every seeded row takes the snapshot's `captured_at` as its `as_of`, because
that is when the release read the feed. A live fetch stamps the moment it
read; a bundled row stamps the moment the release did. Both therefore mean
the same thing, "when the source last told us this", and bundled evidence
ages from the day the snapshot was taken rather than from the day an
installation happened to boot. Stamping the boot instead would make a
year-old snapshot read as fresh evidence on every new install, which is the
one thing a floor must not do.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from importlib import resources
from typing import Final

from pydantic import TypeAdapter

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_CAPABILITY_SOURCE_FAILED
from synthorg.persistence._shared import parse_iso_utc
from synthorg.providers.capability_sources.models import (
    CAPABILITY_AXES,
    CapabilityAxis,
    CapabilityScore,
)

logger = get_logger(__name__)

#: Name of the committed snapshot inside this package.
BUNDLE_FILENAME: Final[str] = "bundled_scores.json"

#: Marker written to a seeded source's status so the dashboard can say the
#: evidence came from the release rather than from a fetch.
BUNDLED_FEED_URL: Final[str] = "bundled snapshot"

#: One row is ``[model_identifier, axis, score]``. A compact array rather
#: than an object per row: the snapshot carries a few thousand measurements
#: and repeating the keys on each would inflate the file for nothing a
#: reader needs. No per-row date, because every row in a snapshot was read
#: at the same moment and that moment is the document's ``captured_at``.
_RowAdapter: Final = TypeAdapter(list[list[str | float]])

_ROW_LENGTH: Final[int] = 3


class BundledSnapshot:
    """The parsed snapshot, keyed by source label.

    Args:
        captured_at: When the snapshot was taken from the live feeds.
        scores: Rows per source label.
    """

    __slots__ = ("_scores", "captured_at")

    def __init__(
        self,
        *,
        captured_at: datetime,
        scores: Mapping[str, tuple[CapabilityScore, ...]],
    ) -> None:
        self.captured_at = captured_at
        self._scores = dict(scores)

    def labels(self) -> tuple[str, ...]:
        """Return the source labels the snapshot covers.

        Returns:
            The labels, sorted.
        """
        return tuple(sorted(self._scores))

    def scores_for(self, label: str) -> tuple[CapabilityScore, ...]:
        """Return the bundled rows for one source.

        Returns:
            The rows, empty when the snapshot does not cover *label*.
        """
        return self._scores.get(label, ())


def _row_to_score(
    row: list[str | float],
    *,
    source_label: str,
    captured_at: datetime,
    ingested_at: datetime,
) -> CapabilityScore | None:
    """Convert one compact snapshot row into a score.

    Returns:
        The score, or ``None`` when the row is mis-shaped. A bad row is
        dropped rather than raised on: a snapshot is a convenience, and
        one corrupt line must not stop an installation from booting.
    """
    if len(row) != _ROW_LENGTH:
        return None
    identifier, axis, score = row
    if not isinstance(identifier, str) or not isinstance(axis, str):
        return None
    if axis not in CAPABILITY_AXES or not isinstance(score, int | float):
        return None
    try:
        return CapabilityScore(
            source_label=NotBlankStr(source_label),
            model_identifier=NotBlankStr(identifier),
            axis=cast_axis(axis),
            score=float(score),
            as_of=captured_at,
            ingested_at=ingested_at,
        )
    except ValueError:
        return None


def cast_axis(axis: str) -> CapabilityAxis:
    """Narrow a validated axis string to its literal type.

    Returns:
        The axis. The caller has already checked membership, so this is a
        narrowing rather than a conversion.
    """
    return CAPABILITY_AXES[CAPABILITY_AXES.index(axis)]


def read_bundled_document() -> str | None:
    """Read the snapshot file shipped inside this package.

    Returns:
        Its text, or ``None`` when it is absent or unreadable.
    """
    try:
        return (
            resources.files("synthorg.providers.capability_sources")
            .joinpath(BUNDLE_FILENAME)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError) as exc:
        logger.warning(
            PROVIDER_CAPABILITY_SOURCE_FAILED,
            operation="load_bundle",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def load_bundled_snapshot(
    *,
    ingested_at: datetime,
    document: str | None = None,
) -> BundledSnapshot | None:
    """Read the snapshot shipped with this release.

    Args:
        ingested_at: Stamped on every row's ``ingested_at``, so a seeded
            score records when this installation adopted it. Its ``as_of``
            comes from the snapshot's own ``captured_at`` instead, so the
            evidence ages from when the release read the feed.
        document: Snapshot text to parse instead of the shipped file. The
            seam exists so the parse can be exercised against a corrupt
            document without planting one inside the installed package.

    Returns:
        The snapshot, or ``None`` when it is absent or unreadable. Absent
        is a degraded state, not a broken one: grading falls back to the
        heuristic exactly as it did before a snapshot shipped.
    """
    raw = document if document is not None else read_bundled_document()
    if raw is None:
        return None
    try:
        parsed_document = json.loads(raw)
        captured_at = parse_iso_utc(str(parsed_document["captured_at"]))
        sources = parsed_document["sources"]
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning(
            PROVIDER_CAPABILITY_SOURCE_FAILED,
            operation="parse_bundle",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if not isinstance(sources, dict):
        # Logged like every other failure here: a snapshot whose ``sources``
        # is a list or a string disables bundled seeding outright, and
        # returning silently leaves grading on the heuristic this package
        # exists to replace with nothing saying why.
        logger.warning(
            PROVIDER_CAPABILITY_SOURCE_FAILED,
            operation="parse_bundle",
            error_type="TypeError",
            error="snapshot 'sources' is not an object",
        )
        return None

    scores: dict[str, tuple[CapabilityScore, ...]] = {}
    for label, rows in sources.items():
        try:
            parsed_rows = _RowAdapter.validate_python(rows)
        except ValueError:
            continue
        converted = [
            score
            for row in parsed_rows
            if (
                score := _row_to_score(
                    row,
                    source_label=str(label),
                    captured_at=captured_at,
                    ingested_at=ingested_at,
                )
            )
            is not None
        ]
        if converted:
            scores[str(label)] = tuple(converted)
    return BundledSnapshot(captured_at=captured_at, scores=scores)


__all__ = [
    "BUNDLED_FEED_URL",
    "BUNDLE_FILENAME",
    "BundledSnapshot",
    "load_bundled_snapshot",
    "read_bundled_document",
]
