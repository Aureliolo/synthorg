"""Derive a per-model :class:`BenchmarkScoreRecord` from an eval scorecard.

The eval spine scores a whole company run; a per-MODEL quality score is
produced by running a single-agent company pinned to the model through
the brief suite (in cassette replay, so it is deterministic and offline)
and projecting the resulting :class:`~evals.models.scorecard.Scorecard`
onto a :class:`~synthorg.budget.benchmark_models.BenchmarkScoreRecord`:

* ``score`` is the suite total normalised to 0..100 (the mean per-brief
  quality).
* the confidence band is the 95 percent normal-approximation interval
  for that mean (``mean +/- 1.96 * standard_error``), clamped to
  ``[0, 100]``. It is derived entirely from the recorded per-brief
  scores, so it is a genuine measurement of the run's spread, never a
  hand-tuned constant. The interval always contains the point estimate,
  so the record's band invariant holds.

These scores are MEASURED from recorded runs, not fitted: the recording
step replays a real cassette, and a model with no recorded cassette is
refused rather than assigned a fabricated number.
"""

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field

from evals.errors import CassetteNotFoundError
from evals.models.scorecard import MAX_PER_BRIEF, Scorecard
from evals.run import run_benchmark_async
from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.core.types import NotBlankStr
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.cassette.mode import CassetteMode
from synthorg.providers.cassette.provider import CassetteCompletionProvider
from synthorg.providers.cassette.redaction import PatternRedactor
from synthorg.providers.cassette.store import CassetteSession

#: Provenance identifier for measured scores. The leading ``benchmark:``
#: flips the dashboard badge from illustrative to measured.
BENCHMARK_SCORE_SOURCE: Final[str] = "benchmark:measured-v1"

#: Standard-normal quantile for a two-sided 95 percent interval.
_Z_95: Final[float] = 1.96


def _clamp_unit_scale(value: float) -> float:
    """Clamp a score to the inclusive ``[0, 100]`` range.

    Returns:
        The clamped value.
    """
    return max(0.0, min(100.0, value))


def score_record_from_scorecard(
    scorecard: Scorecard,
    *,
    model_id: NotBlankStr,
    generated_at: datetime,
    source: str = BENCHMARK_SCORE_SOURCE,
) -> BenchmarkScoreRecord:
    """Project a scorecard onto a measured :class:`BenchmarkScoreRecord`.

    Args:
        scorecard: The recorded single-agent run for ``model_id``.
        model_id: The model the scorecard was measured for.
        generated_at: UTC timestamp to stamp as ``last_updated``.
        source: Provenance identifier (defaults to the measured tag).

    Returns:
        The derived record. ``score`` is the normalised suite mean and
        the confidence band is the 95 percent interval for that mean.
    """
    per_brief = [b.score / MAX_PER_BRIEF * 100.0 for b in scorecard.briefs]
    mean = scorecard.total / scorecard.max_total * 100.0
    if len(per_brief) > 1:
        standard_error = statistics.stdev(per_brief) / (len(per_brief) ** 0.5)
    else:
        standard_error = 0.0
    half_width = _Z_95 * standard_error
    lower = _clamp_unit_scale(mean - half_width)
    upper = _clamp_unit_scale(mean + half_width)
    # The clamp can pull a bound past the mean only when the mean itself
    # is outside [0, 100], which cannot happen for a normalised mean of
    # in-range per-brief scores; guard anyway so the band always contains
    # the estimate and the record invariant holds.
    lower = min(lower, mean)
    upper = max(upper, mean)
    return BenchmarkScoreRecord(
        model_id=model_id,
        score=_clamp_unit_scale(mean),
        confidence_lower=lower,
        confidence_upper=upper,
        source=NotBlankStr(source),
        suite_version=scorecard.suite_version,
        cassette_sha256=scorecard.cassette_sha256,
        last_updated=generated_at,
    )


async def score_model_from_cassette(  # noqa: PLR0913 -- explicit per-model recording inputs
    *,
    model_id: NotBlankStr,
    company_config: Path,
    brief_suite: Path,
    cassette: Path,
    out_dir: Path,
    provider_name: str,
    generated_at: datetime,
    inner_provider: BaseCompletionProvider | None = None,
) -> BenchmarkScoreRecord:
    """Run the brief suite for ``model_id`` and project the score.

    Replay mode (``inner_provider`` is ``None``) replays the recorded
    cassette and refuses a missing one rather than fabricating a score.
    Record mode (``inner_provider`` set) wraps the real driver, records
    the cassette, and flushes it. Both pin the agent to ``model_id`` so
    the recorded and replayed request keys agree.

    Args:
        model_id: The measured model (pins the agent and keys the score).
        company_config: Single-agent company config YAML.
        brief_suite: Directory of brief YAML files.
        cassette: Recorded cassette path (read in replay, written in record).
        out_dir: Directory the scorecard JSON + Markdown are written to.
        provider_name: Stable provider label, identical across record/replay.
        generated_at: UTC timestamp stamped as the record's ``last_updated``.
        inner_provider: Real driver for record mode; ``None`` replays.

    Returns:
        The measured :class:`BenchmarkScoreRecord` for ``model_id``.

    Raises:
        CassetteNotFoundError: In replay mode when the cassette is absent.
    """
    mode = CassetteMode.RECORD if inner_provider is not None else CassetteMode.REPLAY
    # One-shot existence guard before the run begins (not an event-loop hot
    # path); a missing cassette must fail closed, never fabricate a score.
    if mode is CassetteMode.REPLAY and not cassette.exists():  # noqa: ASYNC240
        msg = (
            f"no recorded cassette for {model_id!r} at {cassette}; record it first "
            f"(scores are measured from real runs, never fabricated)"
        )
        raise CassetteNotFoundError(msg)
    session = CassetteSession(mode=mode, path=cassette, redactor=PatternRedactor())
    provider = CassetteCompletionProvider(
        inner=inner_provider,
        session=session,
        provider_name=provider_name,
    )
    scorecard = await run_benchmark_async(
        company_config=company_config,
        brief_suite=brief_suite,
        out_dir=out_dir,
        provider=provider,
        cassette=cassette,
        model_id=model_id,
    )
    if mode is CassetteMode.RECORD:
        await session.flush()
    return score_record_from_scorecard(
        scorecard,
        model_id=model_id,
        generated_at=generated_at,
    )


class BenchmarkModelEntry(BaseModel):
    """One measured model in the recording manifest.

    Attributes:
        model_id: Canonical model id (pins the agent, keys the score).
        company_config: Single-agent company config YAML path.
        cassette: Recorded-cassette path (relative to the repo root).
        provider_name: Stable provider label, identical across
            record/replay.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    model_id: NotBlankStr
    company_config: NotBlankStr
    cassette: NotBlankStr
    provider_name: NotBlankStr


class BenchmarkScoringManifest(BaseModel):
    """The ``models.yaml`` recording manifest.

    Attributes:
        brief_suite: Directory of brief YAML files every model is scored
            against.
        models: The measured models (multiple per tier for true
            per-model granularity).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    brief_suite: NotBlankStr
    models: tuple[BenchmarkModelEntry, ...] = Field(min_length=1)


def load_manifest(path: Path) -> BenchmarkScoringManifest:
    """Parse and validate the recording manifest YAML.

    Returns:
        The validated :class:`BenchmarkScoringManifest`.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return BenchmarkScoringManifest.model_validate(raw)


def serialise_seed_records(records: tuple[BenchmarkScoreRecord, ...]) -> str:
    """Serialise records to the committed seed-artifact JSON string.

    The format is the list ``load_seed_records`` reads: one
    ``model_dump(mode="json")`` object per record, ordered by ``model_id``.

    Returns:
        A pretty-printed JSON document ending in a trailing newline.
    """
    ordered = sorted(records, key=lambda r: r.model_id)
    payload = [r.model_dump(mode="json") for r in ordered]
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


__all__ = [
    "BENCHMARK_SCORE_SOURCE",
    "BenchmarkModelEntry",
    "BenchmarkScoringManifest",
    "load_manifest",
    "score_model_from_cassette",
    "score_record_from_scorecard",
    "serialise_seed_records",
]
