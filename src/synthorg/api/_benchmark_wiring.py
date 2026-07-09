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

from synthorg.api.state import AppState
from synthorg.budget.benchmark_models import BenchmarkScoreRecord
from synthorg.budget.benchmark_protocol import BenchmarkScoreProvider
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.benchmark_score_protocol import BenchmarkScoreRepository

if TYPE_CHECKING:
    from synthorg.budget.forecast_history import CostTrackerHistoryLookup
    from synthorg.budget.pareto_assignments import AgentRegistryAssignmentLookup
    from synthorg.budget.tracker import CostTracker

logger = get_logger(__name__)


def build_benchmark_score_repo(app_state: AppState) -> BenchmarkScoreRepository:
    """Build the per-backend measured-benchmark-score repository.

    Returns:
        The SQLite or Postgres :class:`BenchmarkScoreRepository`.
    """
    from synthorg.persistence.backend_dispatch import (  # noqa: PLC0415
        build_for_backend,
    )
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)

    def _build_sqlite() -> BenchmarkScoreRepository:
        from synthorg.persistence.sqlite.benchmark_score_repo import (  # noqa: PLC0415
            SQLiteBenchmarkScoreRepository,
        )

        return SQLiteBenchmarkScoreRepository(
            sqlite_connection(persistence),
            write_context=persistence.write_context,
        )

    def _build_postgres() -> BenchmarkScoreRepository:
        from synthorg.persistence.postgres.benchmark_score_repo import (  # noqa: PLC0415
            PostgresBenchmarkScoreRepository,
        )

        return PostgresBenchmarkScoreRepository(postgres_pool(persistence))

    return build_for_backend(
        persistence, sqlite=_build_sqlite, postgres=_build_postgres
    )


def select_benchmark_provider(
    strategy: str,
    *,
    repo: BenchmarkScoreRepository,
) -> BenchmarkScoreProvider:
    """Select the benchmark-score provider from the config discriminator.

    ``measured`` reads measured per-model scores from ``repo``; a model
    with no measured row returns ``None`` so the quality axis renders as
    explicitly absent, never faked. An unknown discriminator fails
    loudly rather than silently degrading.

    Args:
        strategy: The ``budget.benchmark_provider`` discriminator value.
        repo: The measured benchmark-score repository.

    Returns:
        The selected :class:`BenchmarkScoreProvider`.

    Raises:
        UnknownBenchmarkProviderError: If ``strategy`` is not a known
            discriminator value.
    """
    from synthorg.budget.benchmark_measured import (  # noqa: PLC0415
        MeasuredBenchmarkScoreProvider,
    )

    if strategy == "measured":
        return MeasuredBenchmarkScoreProvider(repo)
    from synthorg.budget.errors import (  # noqa: PLC0415
        UnknownBenchmarkProviderError,
    )

    msg = f"Unknown budget.benchmark_provider {strategy!r}; expected 'measured'"
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="benchmark_scores",
            note="benchmark-score seeding failed; unseeded models render as absent",
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


def build_pareto_inputs(
    app_state: AppState,
) -> tuple[
    AgentRegistryAssignmentLookup | None,
    CostTrackerHistoryLookup | None,
    CostTracker | None,
]:
    """Resolve the live roster + spend lookups for the Pareto frontier.

    Sources the frontier and the forecaster's history from the live roster
    and observed spend so they render real downgrade candidates / warm
    forecasts instead of empty defaults. Also attaches the durable
    project-cost write + restart-safe dedup repos onto the cost tracker now
    that persistence is connected (the tracker is built at the synchronous
    construction phase before a backend exists; the dedup guard makes the
    increment idempotent across a JetStream redelivery after a restart). A
    registry/tracker absent at wiring time leaves both lookups ``None``
    (cold-start forecaster, empty-frontier analyzer) rather than poisoning
    startup.

    Returns:
        ``(assignment_lookup, history_lookup, cost_tracker)``.
    """
    from synthorg.budget.forecast_history import (  # noqa: PLC0415
        CostTrackerHistoryLookup,
    )
    from synthorg.budget.pareto_assignments import (  # noqa: PLC0415
        AgentRegistryAssignmentLookup,
    )
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)
    registry = app_state.slice(HrStateSlice).agent_registry
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    if cost_tracker is not None:
        cost_tracker.attach_durable_repos(
            project_cost_repo=persistence.project_cost_aggregates,
            claim_seen_repo=persistence.project_cost_claim_seen,
        )
    if registry is None or cost_tracker is None:
        return None, None, cost_tracker
    assignment_lookup = AgentRegistryAssignmentLookup(
        registry=registry,
        cost_tracker=cost_tracker,
        clock=app_state.clock.now,
    )
    history_lookup = CostTrackerHistoryLookup(
        registry=registry,
        cost_tracker=cost_tracker,
        clock=app_state.clock.now,
    )
    return assignment_lookup, history_lookup, cost_tracker


async def rebuild_cost_dial_benchmark_provider(app_state: AppState) -> None:
    """Rebuild the benchmark provider + Pareto analyzer from live settings.

    Hot-reload counterpart to the ``benchmark_provider`` / ``model_tier_overrides``
    slice of ``_wire_cost_dial_services``. Resolves both settings through the
    live chain (DB > env > default) -- the boot path reads them off ``BudgetConfig``
    mirrors (bootstrap, env > default), which cannot see a DB override -- then
    rebuilds the provider and the Pareto analyzer and re-wires them onto
    ``BudgetStateSlice``. The engine routing strategy that also reads the slice
    provider is refreshed separately by the runtime-services reload the subscriber
    triggers after this call.

    No-op when the cost-dial services are not wired (no resolver / no budget
    config / no benchmark repo) so a dev/test rig without persistence is safe.
    """
    from synthorg.budget.model_tier import ModelTierMap  # noqa: PLC0415
    from synthorg.budget.pareto import ParetoAnalyzer  # noqa: PLC0415
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.settings.enums import SettingNamespace  # noqa: PLC0415
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        config_resolver_of,
    )

    budget_slice = app_state.slice(BudgetStateSlice)
    budget_config = budget_slice.budget_config
    repo = budget_slice.benchmark_score_repo
    if (
        app_state.slice(SettingsStateSlice).config_resolver is None
        or budget_config is None
        or repo is None
    ):
        return
    resolver = config_resolver_of(app_state)
    strategy = await resolver.get_str(
        SettingNamespace.BUDGET.value, "benchmark_provider"
    )
    overrides = await resolver.get_json(
        SettingNamespace.BUDGET.value, "model_tier_overrides"
    )
    model_tier_map = ModelTierMap(overrides=overrides)
    benchmark_provider = select_benchmark_provider(strategy, repo=repo)
    assignment_lookup, _history_lookup, _cost_tracker = build_pareto_inputs(app_state)
    analyzer = ParetoAnalyzer(
        benchmark_provider=benchmark_provider,
        budget_config=budget_config,
        assignment_lookup=assignment_lookup,
        model_tier_map=model_tier_map,
    )
    app_state.wire(
        BudgetStateSlice,
        benchmark_provider=benchmark_provider,
        pareto_analyzer=analyzer,
    )


__all__ = [
    "build_benchmark_score_repo",
    "build_pareto_inputs",
    "rebuild_cost_dial_benchmark_provider",
    "seed_benchmark_scores",
    "select_benchmark_provider",
]
