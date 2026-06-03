"""Benchmark-score provider + repository wiring for the cost-dial.

Extracted from :mod:`synthorg.api._app_wiring` so the per-backend
``BenchmarkScoreRepository`` build, the ``budget.benchmark_provider``
discriminator selection, and the boot-time seed of the measured scores
live together as one cohesive unit instead of crowding the broader
cost-dial wiring helper.

The provider selection fails loudly on an unknown discriminator; the
seed step is idempotent and best-effort (it never poisons startup).
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.benchmark_protocol import BenchmarkScoreProvider
    from synthorg.persistence.benchmark_score_protocol import BenchmarkScoreRepository

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
) -> BenchmarkScoreProvider:
    """Select the benchmark-score provider from the config discriminator.

    ``stub`` (the safe default) returns calibrated per-tier constants;
    ``measured`` reads measured per-model scores from ``repo`` and falls
    back to the stub for any unmeasured model. An unknown discriminator
    fails loudly rather than silently degrading to the stub.

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
        return StubBenchmarkScoreProvider()
    if strategy == "measured":
        return MeasuredBenchmarkScoreProvider(
            repo,
            fallback=StubBenchmarkScoreProvider(),
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
    operator database carries them without a recording run. Idempotent:
    a non-empty table is left untouched so operator-recorded scores are
    never clobbered. Only runs in the ``measured`` arm; the stub arm has
    no repo to seed. Best-effort: a seeding failure logs and is swallowed
    so it cannot poison startup.
    """
    from synthorg.budget.benchmark_seed import load_seed_records  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415

    budget_slice = app_state.slice(BudgetStateSlice)
    repo = budget_slice.benchmark_score_repo
    config = budget_slice.budget_config
    if repo is None or config is None or config.benchmark_provider != "measured":
        return
    try:
        if await repo.list_items(limit=1):
            return
        records = load_seed_records()
        for record in records:
            await repo.save(record)
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
    if records:
        logger.info(
            API_APP_STARTUP,
            service="benchmark_scores",
            note="seeded measured scores from committed artifact",
            count=len(records),
        )


__all__ = [
    "build_benchmark_score_repo",
    "seed_benchmark_scores",
    "select_benchmark_provider",
]
