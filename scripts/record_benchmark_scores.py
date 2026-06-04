"""Record/replay per-model benchmark cassettes and write measured scores.

This is the offline entry point behind ``make record-benchmark-scores``. It
reads the recording manifest (``evals/benchmark_scores/models.yaml``), runs the
brief suite once per measured model, projects each run's scorecard onto a
:class:`~synthorg.budget.benchmark_models.BenchmarkScoreRecord`, and writes the
committed seed artifact the API boot-seeds the benchmark-score repository from.

Two modes:

* **replay** (default): replays each model's committed cassette. Deterministic
  and offline; refuses a model whose cassette is missing rather than fabricating
  a score.
* **record** (``--record``): wraps the real provider named in the manifest
  (built from the model's company config), runs against it (real spend), and
  persists the cassette for future replays. The maintainer runs this once with
  real provider credentials, aliasing the ``example-*`` ids to real models, then
  commits the cassettes and the regenerated seed artifact.

Scores are MEASURED from real recorded runs, never fitted.
"""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from evals.benchmark_scoring import (
    BenchmarkModelEntry,
    load_manifest,
    score_model_from_cassette,
    serialise_seed_records,
)
from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.config.loader import load_config
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_BENCHMARK_SCORE_RECORDED
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)

_DEFAULT_MANIFEST = Path("evals/benchmark_scores/models.yaml")
_DEFAULT_SEED_OUT = Path("src/synthorg/budget/benchmark_seed.json")
_DEFAULT_OUT_DIR = Path(".benchmark/scores")


def _build_inner_provider(entry: BenchmarkModelEntry) -> BaseCompletionProvider:
    """Build the real driver for *entry* from its company config (record mode).

    Returns:
        The configured driver for ``entry.provider_name``.
    """
    root_config = load_config(Path(entry.company_config))
    registry = ProviderRegistry.from_config(root_config.providers)
    return registry.get(entry.provider_name)


async def _score_entry(
    entry: BenchmarkModelEntry,
    *,
    brief_suite: Path,
    out_dir: Path,
    record: bool,
    generated_at: datetime,
) -> BenchmarkScoreRecord:
    """Run (replay or record) one model and return its measured record.

    Returns:
        The measured :class:`BenchmarkScoreRecord` for ``entry.model_id``.
    """
    inner = _build_inner_provider(entry) if record else None
    return await score_model_from_cassette(
        model_id=NotBlankStr(entry.model_id),
        company_config=Path(entry.company_config),
        brief_suite=brief_suite,
        cassette=Path(entry.cassette),
        out_dir=out_dir / entry.model_id,
        provider_name=entry.provider_name,
        generated_at=generated_at,
        inner_provider=inner,
    )


async def _run(args: argparse.Namespace) -> int:
    """Score every manifest model and write the seed artifact.

    Returns:
        Process exit code (0 on success).
    """
    manifest = load_manifest(args.manifest)
    brief_suite = Path(manifest.brief_suite)
    generated_at = datetime.now(UTC)
    records: list[BenchmarkScoreRecord] = []
    for entry in manifest.models:
        record = await _score_entry(
            entry,
            brief_suite=brief_suite,
            out_dir=args.out_dir,
            record=args.record,
            generated_at=generated_at,
        )
        records.append(record)
        logger.info(
            EVALS_BENCHMARK_SCORE_RECORDED,
            model_id=record.model_id,
            score=record.score,
            source=record.source,
            mode="record" if args.record else "replay",
        )
    args.seed_out.parent.mkdir(parents=True, exist_ok=True)
    args.seed_out.write_text(serialise_seed_records(tuple(records)), encoding="utf-8")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the recording CLI arguments.

    Returns:
        The parsed namespace.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--seed-out", type=Path, default=_DEFAULT_SEED_OUT)
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR)
    parser.add_argument(
        "--record",
        action="store_true",
        help="Wrap the real provider and record cassettes (real spend).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code.
    """
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
