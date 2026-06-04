# module-kind: code
"""Benchmark-score provider + repository wiring for the cost-dial.

Groups the per-backend ``BenchmarkScoreRepository`` build, the
``budget.benchmark_provider`` discriminator selection, and the boot-time
seed of the measured scores into one cohesive unit so the broader
cost-dial wiring helper stays focused on the forecaster + Pareto axis.

The provider selection fails loudly on an unknown discriminator; the
seed step is idempotent and best-effort (it never poisons startup), and
a corrupt committed seed artifact is surfaced at ERROR rather than being
masked as a transient failure.
"""

from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.budget.benchmark_protocol import BenchmarkScoreProvider
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.benchmark_score_protocol import BenchmarkScoreRepository

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.benchmark_models import BenchmarkScoreRecord
    from synthorg.budget.model_tier import ModelTierMap

logger = get_logger(__name__)


def build_benchmark_score_repo(app_state: AppState) -> BenchmarkScoreRepository:
    """Build the per-backend measured-benchmark-score repository.

    Returns:
        The SQLite or Postgres :class:`BenchmarkScoreRepository`.
    """
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)
    if persistence.backend_name == "sqlite":
        from synthorg.persistence.sqlite.benchmark_score_repo import (  # noqa: PLC0415
            SQLiteBenchmarkScoreRepository,
        )

        return SQLiteBenchmarkScoreRepository(
            sqlite_connection(persistence),
            write_context=persistence.write_context,
        )
    from synthorg.persistence.postgres.benchmark_score_repo import (  # noqa: PLC0415
        PostgresBenchmarkScoreRepository,
    )

    return PostgresBenchmarkScoreRepository(postgres_pool(persistence))


def select_benchmark_provider(
    strategy: str,
    *,
    repo: BenchmarkScoreRepository,
    tier_map: ModelTierMap | None = None,
) -> BenchmarkScoreProvider:
    """Select the benchmark-score provider from the config discriminator.

    ``stub`` (the safe default) returns calibrated per-tier constants;
    ``measured`` reads measured per-model scores from ``repo`` and falls
    back to the stub for any unmeasured model. An unknown discriminator
    fails loudly rather than silently degrading to the stub.

    Args:
        strategy: The ``budget.benchmark_provider`` discriminator value.
        repo: The measured benchmark-score repository.
        tier_map: Operator tier overrides threaded into the stub (both the
            ``stub`` arm and the ``measured`` arm's fallback) so an
            override-only model id resolves its cold-start score.

    Returns:
        The selected :class:`BenchmarkScoreProvider`.

    Raises:
        UnknownBenchmarkProviderError: If ``strategy`` is not a known
            discriminator value.
    """
    from synthorg.budget.benchmark_measured import (  # noqa: PLC0415
        MeasuredBenchmarkScoreProvider,
    )
    from synthorg.budget.benchmark_stub import (  # noqa: PLC0415
        StubBenchmarkScoreProvider,
    )

    if strategy == "stub":
        return StubBenchmarkScoreProvider(tier_map=tier_map)
    if strategy == "measured":
        return MeasuredBenchmarkScoreProvider(
            repo,
            fallback=StubBenchmarkScoreProvider(tier_map=tier_map),
        )
    from synthorg.budget.errors import (  # noqa: PLC0415
        UnknownBenchmarkProviderError,
    )

    msg = (
        f"Unknown budget.benchmark_provider {strategy!r}; expected 'stub' or 'measured'"
    )
    raise UnknownBenchmarkProviderError(msg)


async def seed_benchmark_scores(app_state: AppState) -> None:
    """Seed the benchmark-score repo from the committed artifact when empty.

    Populates the measured per-model scores recorded offline so a fresh
    operator database carries them without a recording run. Idempotent
    and convergent: each committed seed row is written only when its
    ``model_id`` is absent, so an operator-recorded score (even one that
    re-measures a seed model) is never clobbered, and a seed left partial
    by an interrupted or raced earlier boot is completed on the next boot
    rather than skipped forever. Only runs in the ``measured`` arm; the
    stub arm has no repo to seed. Best-effort: a seeding failure logs and
    is swallowed so it cannot poison startup.
    """
    from synthorg.budget.benchmark_seed import load_seed_records  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415

    budget_slice = app_state.slice(BudgetStateSlice)
    repo = budget_slice.benchmark_score_repo
    config = budget_slice.budget_config
    if repo is None or config is None or config.benchmark_provider != "measured":
        return
    records: tuple[BenchmarkScoreRecord, ...] = ()
    seeded = 0
    try:
        records = load_seed_records()
        for record in records:
            # Per-row presence check rather than a single "table non-empty"
            # short-circuit: the latter treats a partial seed (boot died
            # mid-loop, or a concurrent boot lost a duplicate-key race) as
            # complete and skips the remainder forever. Writing only absent
            # rows converges to the full artifact across boots while leaving
            # operator-recorded scores untouched.
            if await repo.get(record.model_id) is not None:
                continue
            await repo.save(record)
            seeded += 1
    except (ValueError, ValidationError) as exc:
        # A malformed committed artifact never self-heals on the next
        # boot, so it is an operator-actionable defect (ERROR), not the
        # transient degradation the broad handler below covers.
        logger.error(
            API_APP_STARTUP,
            service="benchmark_scores",
            note=(
                "benchmark seed artifact is corrupt; regenerate via"
                " scripts/record_benchmark_scores.py"
            ),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="benchmark_scores",
            note="benchmark-score seeding failed; measured scores fall back to stub",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    if seeded:
        logger.info(
            API_APP_STARTUP,
            service="benchmark_scores",
            note="seeded measured scores from committed artifact",
            count=seeded,
        )
    elif records:
        logger.debug(
            API_APP_STARTUP,
            service="benchmark_scores",
            note="measured scores already present; seed left untouched",
        )
    else:
        logger.debug(
            API_APP_STARTUP,
            service="benchmark_scores",
            note="no measured scores to seed; table stays empty until a recording run",
        )


__all__ = [
    "build_benchmark_score_repo",
    "seed_benchmark_scores",
    "select_benchmark_provider",
]
