# module-kind: adapter
"""Parser for the Epoch AI benchmark CSV.

The feed is one row per ``(model, benchmark)`` measurement, with the header
``model_id, benchmark_id, performance, benchmark, benchmark_release_date,
optimized, model, model_version, Model, model_group, Model aggregation,
Model Aggregation Date, date, source``.

Four of those columns carry everything this layer needs, and the parser
requires exactly those four so a feed reshuffle fails loudly instead of
producing plausible nonsense:

``source``
    Who produced the number, and the reason this parser reads only part of
    its own feed. The hub is a blend: rows marked ``Epoch evaluations``
    are ones Epoch ran itself under a single harness, and the rest are
    either another leaderboard's numbers or a vendor's own technical
    report about its own model. Only the first is admitted.

    A vendor grading its own model is the evidence class this whole layer
    exists to replace, and it is not a small slice: ``MMLU``, ``GSM8K``,
    ``HellaSwag``, ``BBH``, ``PIQA``, ``Winogrande`` and their neighbours
    reach this feed exclusively through technical reports. Admitting them
    let a 7B coding model rank top of the reasoning axis on a single
    self-reported grade-school-maths score.

``model_version``
    The vendor's own model id (``amazon.nova-pro-v1:0``), which is what a
    provider config also names. The human-facing ``model`` column is
    deliberately ignored: a display name resolves to a configured pair
    only by guessing, and a matcher that guesses is how a wrong grade
    gets in.

    For a model with a reasoning dial the feed appends the setting it
    evaluated under (``..._high``, ``..._xhigh``, ``..._32k``,
    ``..._promax`` on a pro variant, and ``..._unknown`` where it could
    not tell), and such a row is dropped.
    It names a CONFIGURATION, and this product binds a model rather than
    a configuration: reasoning effort is a per-task dial here
    (``StakesReasoning``), so no one setting is the one we would call it
    with. Keeping the row would be worse than losing it in two ways. It
    can never match a configured pair, so it grades nothing; and it still
    occupies a slot in the cohort every rung is a rank within, where one
    model evaluated at a dozen settings quietly counts a dozen times.
    Measured against the shipped snapshot that was 59% of the cohort and
    left 13% of the matchable models a rung too low.
``performance``
    The measurement, published on 0-1 and normalised here to 0-100.
``benchmark``
    Mapped onto an axis by :mod:`..axis_map`. A benchmark that table does
    not recognise is skipped rather than filed under a guess: the axis is
    ranked as a cohort, so a misfiled row moves every model's standing
    rather than sitting harmlessly at the edge.
**The feed carries no measurement date, so ``as_of`` is when we read it.**
The ``date`` column looks like one and is not: every model carries a single
date across every benchmark it appears on (290 of 290 checked),
``gemini-2.5-flash-preview-04-17`` is dated ``2025-04-17``, and
``gemini-2.5-flash`` is dated two weeks BEFORE the FrontierMath tier it is
scored on was published. It is the model's release date. Feeding it to
``as_of`` made the dashboard's evidence age into model age and made the
recency cut retire old models rather than old evidence. Since the source
publishes nothing better, ``as_of`` records the read instead, which is a
claim we can actually stand behind: it says how long ago the source last
told us this, and it ages exactly when refreshes stop landing.

Several benchmarks land on one axis, so their scores are averaged into a
single per-axis figure. The mean is the honest reduction: taking the best
would let one flattering benchmark speak for the axis, and taking the
worst would let one adversarial benchmark do the same.
"""

import csv
import io
import math
import re
from collections import defaultdict
from datetime import datetime
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
_SOURCE_COLUMN: Final[str] = "source"

_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    _MODEL_COLUMN,
    _SCORE_COLUMN,
    _BENCHMARK_COLUMN,
    _SOURCE_COLUMN,
)

#: The one ``source`` value naming a row Epoch evaluated itself. An exact
#: match, never a prefix or a substring: the neighbouring values are free
#: text naming other people's leaderboards and vendors' own papers, and a
#: loose match there admits exactly what this filter exists to exclude.
_MEASURED_BY_SOURCE: Final[str] = "Epoch evaluations"

#: The feed publishes ``performance`` as a 0-1 fraction; scores are stored
#: on 0-100 so two sources with different native ranges compare.
_FRACTION_TO_PERCENT: Final[float] = 100.0

#: An evaluation-configuration suffix on ``model_version``: the reasoning
#: effort (optionally on a ``pro`` variant) or the thinking budget the run
#: used. Enumerated from the whole feed rather than matching "anything
#: after the last underscore", because plenty of real model ids end in one:
#: a version (``phi-1_5``), a parameter count (``open_llama_7b``,
#: ``Qwen-1_8B``), or a name that simply contains the character. A budget
#: is therefore ``\d+k`` and never ``\d+b``, which is the distinction
#: between a thinking budget and a model's size.
_CONFIG_SUFFIX: Final[re.Pattern[str]] = re.compile(
    r"_(?:(?:pro)?(?:minimal|none|low|medium|high|xhigh|max|thinking|unknown)|\d+k)$",
    re.IGNORECASE,
)


def names_a_configuration(model_version: str) -> bool:
    """Report whether an identifier names a run setting, not a model.

    Returns:
        ``True`` when the identifier carries an evaluation-configuration
        suffix, which makes it unbindable and therefore ungradable.
    """
    return _CONFIG_SUFFIX.search(model_version.strip()) is not None


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
    if not math.isfinite(fraction):
        # ``float("NaN")`` parses and then passes both range checks, because
        # every comparison against it is false. ``CapabilityScore`` forbids
        # it, so it would abort the whole parse instead of counting as one
        # skipped row.
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
    rows_read = 0
    rows_skipped = 0

    for row in reader:
        rows_read += 1
        model_id = (row.get(_MODEL_COLUMN) or "").strip()
        score = _parse_score(row.get(_SCORE_COLUMN) or "")
        if (
            not model_id
            or score is None
            or (row.get(_SOURCE_COLUMN) or "").strip() != _MEASURED_BY_SOURCE
            or names_a_configuration(model_id)
        ):
            rows_skipped += 1
            continue
        axis = axis_for_benchmark(row.get(_BENCHMARK_COLUMN) or "")
        if axis is None:
            rows_skipped += 1
            continue
        totals[(model_id, axis)].append(score)

    scores = tuple(
        CapabilityScore(
            source_label=NotBlankStr(source_label),
            model_identifier=NotBlankStr(model_id),
            axis=axis,
            score=sum(values) / len(values),
            as_of=ingested_at,
            ingested_at=ingested_at,
        )
        for (model_id, axis), values in sorted(totals.items())
    )
    return ParsedFeed(
        scores=scores,
        rows_read=rows_read,
        rows_skipped=rows_skipped,
    )


__all__ = ["names_a_configuration", "parse_epoch_csv"]
