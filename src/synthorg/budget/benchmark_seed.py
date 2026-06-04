# module-kind: code
"""Loader for the committed measured benchmark-score seed artifact.

The offline recording entry-point (``scripts/record_benchmark_scores.py``)
writes the measured per-model scores to ``benchmark_seed.json`` alongside
this module. At startup, ``_wire_cost_dial_services`` seeds the
benchmark-score repository from this artifact when the table is empty so
a fresh operator database carries the measured scores without a recording
run. The artifact is committed and packaged with the source tree, so it
is readable at runtime (unlike the out-of-package ``evals/`` tree the
cassettes live in).
"""

import json
from pathlib import Path

from pydantic import ValidationError

from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import (
    BUDGET_BENCHMARK_SEED_ABSENT,
    BUDGET_BENCHMARK_SEED_MALFORMED,
)

logger = get_logger(__name__)

_SEED_PATH = Path(__file__).with_name("benchmark_seed.json")


def load_seed_records(path: Path | None = None) -> tuple[BenchmarkScoreRecord, ...]:
    """Load the committed measured-score seed records.

    Args:
        path: Override for the seed-artifact path (tests). Defaults to
            the packaged ``benchmark_seed.json``.

    Returns:
        The parsed seed records; an empty tuple when the artifact is
        absent or holds an empty list.

    Raises:
        ValueError: When the artifact is present but malformed (a
            committed seed is expected to be valid, so a parse failure is
            surfaced rather than silently swallowed).
        ValidationError: When a seed row fails schema validation; the
            malformed-seed event is logged with the offending row index
            before the error propagates.
    """
    seed_path = path if path is not None else _SEED_PATH
    if not seed_path.exists():
        # A legitimate state for a fresh checkout, but logged so an
        # artifact dropped by a packaging mistake stays observable rather
        # than reading as "no scores recorded yet".
        logger.debug(BUDGET_BENCHMARK_SEED_ABSENT, path=str(seed_path))
        return ()
    raw = seed_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            BUDGET_BENCHMARK_SEED_MALFORMED,
            path=str(seed_path),
            reason="not valid JSON",
        )
        msg = f"benchmark seed artifact {seed_path} is not valid JSON"
        raise ValueError(msg) from exc
    if not isinstance(payload, list):
        # Uniform malformed-artifact error type (matches the decode failure
        # above); the loader's contract is "ValueError on a malformed seed".
        logger.warning(
            BUDGET_BENCHMARK_SEED_MALFORMED,
            path=str(seed_path),
            reason="top-level JSON is not a list",
        )
        msg = f"benchmark seed artifact {seed_path} must be a JSON list of records"
        raise ValueError(msg)  # noqa: TRY004
    records: list[BenchmarkScoreRecord] = []
    for index, entry in enumerate(payload):
        try:
            records.append(BenchmarkScoreRecord.model_validate(entry))
        except ValidationError as exc:
            # Match the malformed-artifact event the JSON-decode and
            # not-a-list paths already emit so a bad committed row keeps
            # the artefact path + row context instead of raising a bare
            # ValidationError with neither.
            logger.warning(
                BUDGET_BENCHMARK_SEED_MALFORMED,
                path=str(seed_path),
                reason="row failed validation",
                row_index=index,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
    return tuple(records)


__all__ = ["load_seed_records"]
