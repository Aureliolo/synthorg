"""Toolsmith (self-extending toolkit) construction + startup wiring.

Groups the backend-specific authored-tool repository builder, the
toolsmith-runtime factory, and the startup step that wires the toolsmith once
a provider and connected persistence are present, so the composition root
stays a thin caller.
"""

import asyncio
import contextlib
from pathlib import Path

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.toolsmith.cycle_scheduler import ToolsmithCycleScheduler
from synthorg.meta.toolsmith.factory import ToolsmithRuntime
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.protocol import GoldenScorecardProvider
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository
from synthorg.providers.registry import ProviderRegistry
from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)


def _build_dynamic_tool_repo(
    persistence: PersistenceBackend,
) -> DynamicToolRepository:
    """Build the backend-specific authored-tool blueprint repository.

    Returns:
        The SQLite or Postgres dynamic-tool blueprint repository.
    """
    from synthorg.persistence.backend_dispatch import (  # noqa: PLC0415
        build_for_backend,
    )

    def _build_sqlite() -> DynamicToolRepository:
        from synthorg.persistence.db_handle import (  # noqa: PLC0415
            sqlite_connection,
        )
        from synthorg.persistence.sqlite.tool_blueprint_repo import (  # noqa: PLC0415
            SQLiteDynamicToolRepository,
        )

        return SQLiteDynamicToolRepository(
            sqlite_connection(persistence),
            write_context=persistence.write_context,
        )

    def _build_postgres() -> DynamicToolRepository:
        from synthorg.persistence.db_handle import postgres_pool  # noqa: PLC0415
        from synthorg.persistence.postgres.tool_blueprint_repo import (  # noqa: PLC0415
            PostgresDynamicToolRepository,
        )

        return PostgresDynamicToolRepository(postgres_pool(persistence))

    return build_for_backend(
        persistence, sqlite=_build_sqlite, postgres=_build_postgres
    )


def _build_toolsmith_runtime(
    *,
    app_state: AppState,
    si_config: SelfImprovementConfig,
    provider_registry: ProviderRegistry,
    persistence: PersistenceBackend,
    approval_store: ApprovalStoreProtocol | None,
    cost_tracker: CostTrackerProtocol | None,
) -> ToolsmithRuntime:
    """Resolve dependencies and build the toolsmith runtime.

    The sandbox resolver maps each blueprint's declared backend to
    a concrete sandbox built from the default sandboxing config, so a
    Docker-declared authored tool runs under Docker and a subprocess one
    under subprocess. The sandbox workspace pins to the app's resolved
    workspace root (the same root the project-workspace service uses) so
    authored tools and the rest of the runtime share one writable mount
    instead of diverging on the process CWD. The golden-scorecard provider
    is selected by ``toolsmith.validation.golden_scorecard_provider``:
    ``none`` (the default) wires no provider, so a ``require_golden_delta``
    gate fails closed (a missing provider rejects the apply) rather than
    trusting an unvalidated tool; ``eval`` wires the eval-backed
    :class:`EvalGoldenScorecardProvider` so the gate runs the golden suite
    end-to-end.

    Returns:
        The built toolsmith runtime.
    """
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )
    from synthorg.meta.toolsmith.factory import build_toolsmith  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415
    from synthorg.tools.sandbox.factory import (  # noqa: PLC0415
        build_sandbox_backends,
    )
    from synthorg.tools.sandbox.sandboxing_config import (  # noqa: PLC0415
        SandboxingConfig,
    )

    repo = _build_dynamic_tool_repo(persistence)

    sandboxing = SandboxingConfig()
    backends = build_sandbox_backends(
        config=sandboxing,
        workspace=agent_workspace_root_of(app_state),
        tracked_container_repo=(
            persistence.tracked_containers if persistence.is_connected else None
        ),
    )

    def _resolve_sandbox(blueprint: ToolBlueprint) -> SandboxBackend:
        return backends.get(
            blueprint.sandbox_backend.value, backends[sandboxing.default_backend]
        )

    scorecard_provider = _build_golden_scorecard_provider(
        si_config.toolsmith.validation.golden_scorecard_provider
    )

    # The toolsmith names its own connection at dispatch time, from the
    # operator's ``meta.toolsmith_model`` pair. A provider is a registered
    # connection with its own credentials and endpoint, so the same model id
    # reached through two of them is two different calls, and there is no
    # shared provider to inherit. Handing over the whole registry keeps the
    # assignment live: choosing a model later arms authoring on the next gap
    # rather than the next boot, and an unset one raises where it is used.
    return build_toolsmith(
        si_config=si_config,
        connections=provider_registry.get,
        repo=repo,
        sandbox_resolver=_resolve_sandbox,
        scorecard_provider=scorecard_provider,
        approval_store=approval_store,
        cost_tracker=cost_tracker,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
        notification_dispatcher=app_state.slice(NotificationsStateSlice).dispatcher,
    )


def _locate_evals_root() -> Path:
    """Locate the in-repo golden-company eval harness directory.

    Walks up from the installed ``synthorg`` package looking for an
    ``evals`` directory carrying the reference baseline. The eval harness
    is out-of-package (repo-only), so this resolves only in a source
    checkout.

    Returns:
        The ``evals`` directory containing ``baselines/reference.yaml``.

    Raises:
        GoldenScorecardUnavailableError: No eval harness is on disk.
    """
    import synthorg  # noqa: PLC0415
    from synthorg.meta.toolsmith.errors import (  # noqa: PLC0415
        GoldenScorecardUnavailableError,
    )

    package_root = Path(synthorg.__file__).resolve()
    for base in package_root.parents:
        candidate = base / "evals"
        if (candidate / "baselines" / "reference.yaml").is_file():
            return candidate
    msg = "golden_scorecard_provider='eval' requires the evals/ harness on disk"
    logger.warning(
        API_APP_STARTUP,
        action="golden_scorecard_evals_missing",
        error_type=GoldenScorecardUnavailableError.__name__,
    )
    raise GoldenScorecardUnavailableError(msg)


def _build_golden_scorecard_provider(
    strategy: str,
) -> GoldenScorecardProvider | None:
    """Select the golden-scorecard provider for the validation gate.

    ``none`` wires no provider (the gate fails closed when it requires a
    golden delta); ``eval`` wires the eval-backed provider over the
    golden-company benchmark so the gate runs end-to-end. An unknown
    strategy fails loudly.

    Args:
        strategy: The configured ``golden_scorecard_provider`` discriminator.

    Returns:
        The selected provider, or ``None`` for the ``none`` arm.

    Raises:
        UnknownGoldenScorecardProviderError: ``strategy`` is not a known arm.
        GoldenScorecardUnavailableError: ``eval`` is selected but the
            golden-company eval harness is not present on disk.
    """
    from synthorg.meta.toolsmith.errors import (  # noqa: PLC0415
        UnknownGoldenScorecardProviderError,
    )

    if strategy == "none":
        return None
    if strategy != "eval":
        msg = f"unknown golden_scorecard_provider {strategy!r}; expected none|eval"
        logger.warning(
            API_APP_STARTUP,
            action="unknown_golden_scorecard_provider",
            strategy=strategy,
            error_type=UnknownGoldenScorecardProviderError.__name__,
        )
        raise UnknownGoldenScorecardProviderError(msg)

    from synthorg.meta.toolsmith.golden_scorecard import (  # noqa: PLC0415
        EvalGoldenScorecardProvider,
    )

    evals_root = _locate_evals_root()
    company_config = evals_root / "baselines" / "reference.yaml"
    brief_suite = evals_root / "briefs"
    anchors_dir = evals_root / "anchors"

    async def _run_golden_suite(blueprint: ToolBlueprint | None) -> int:
        """Run the reference golden suite once and return its total.

        The deterministic eval ignores authored tools, so the candidate
        arm is identical to the baseline; the provider is built
        candidate-insensitive and only ever calls this with ``None``.

        Returns:
            The golden suite total (``Scorecard.total``).
        """
        del blueprint
        from tempfile import TemporaryDirectory  # noqa: PLC0415

        from evals.run import run_benchmark_async  # noqa: PLC0415

        with TemporaryDirectory(prefix="synthorg-golden-") as out_dir:
            scorecard = await run_benchmark_async(
                company_config=company_config,
                brief_suite=brief_suite,
                out_dir=Path(out_dir),
                anchors_dir=anchors_dir,
            )
        return scorecard.total

    return EvalGoldenScorecardProvider(run_scorecard=_run_golden_suite)


def _toolsmith_disabled(note: str, exc: Exception) -> None:
    """Record that a toolsmith wiring stage failed, leaving it disabled.

    Args:
        note: What failed, in the terms an operator reads it in.
        exc: The failure, redacted before it reaches the log.
    """
    logger.warning(
        API_APP_STARTUP,
        service="toolsmith",
        note=note,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _install_dynamic_layer(runtime: ToolsmithRuntime) -> bool:
    """Install the toolsmith's layered MCP surface.

    Installed BEFORE the once-only AppState mutation.
    ``set_toolsmith_service`` cannot be replayed on retry, so a layer
    install failing after the mutation leaves the runtime half-wired
    (service present, layer missing) with no path back. Installing first
    means a failure here leaves the toolsmith disabled cleanly.

    Args:
        runtime: The built toolsmith runtime carrying the dynamic registry.

    Returns:
        ``True`` once the layer is installed; ``False`` when it failed and
        the toolsmith stays disabled.
    """
    from synthorg.meta.mcp.server import (  # noqa: PLC0415
        install_dynamic_tool_layer,
    )

    try:
        install_dynamic_tool_layer(runtime.dynamic_registry)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _toolsmith_disabled("toolsmith dynamic layer install failed", exc)
        return False
    return True


async def _start_cycle_scheduler(
    app_state: AppState,
    *,
    runtime: ToolsmithRuntime,
    si_config: SelfImprovementConfig,
) -> bool:
    """Start the periodic detection cycle and publish the toolsmith slice.

    Starting the scheduler is what makes the org propose new tools
    automatically rather than only on a manual trigger. A start that fails
    after the scheduler object exists still owns a task group, so it is
    stopped before the toolsmith is abandoned.

    Args:
        app_state: The application state the slice is published onto.
        runtime: The built toolsmith runtime.
        si_config: The self-improvement config carrying the cycle interval.

    Returns:
        ``True`` once the scheduler runs and the slice is published;
        ``False`` when either failed and the toolsmith stays disabled.

    Raises:
        CancelledError: Propagated once the half-started scheduler is
            stopped, so a shutdown mid-start leaves no orphaned task group.
    """
    from synthorg.meta.toolsmith.state import (  # noqa: PLC0415
        ToolsmithStateSlice,
    )
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    scheduler = ToolsmithCycleScheduler(
        runtime.service,
        interval_seconds=si_config.toolsmith.cycle_interval_seconds,
        config_resolver=config_resolver_of(app_state),
        approval_consumer=runtime.approval_consumer,
    )
    try:
        await scheduler.start()
        app_state.swap_slice(
            ToolsmithStateSlice(service=runtime.service, cycle_scheduler=scheduler),
        )
    except asyncio.CancelledError:
        # Cancellation is a BaseException, so the handler below never sees
        # it: a shutdown landing inside ``start()`` would leave the
        # scheduler's task group running with nothing holding a reference
        # to stop it. Shielded, because that same cancellation would cancel
        # the stop as well.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(_stop_quietly(scheduler))
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        await _stop_quietly(scheduler)
        _toolsmith_disabled("toolsmith scheduler wiring failed", exc)
        return False
    return True


async def _stop_quietly(scheduler: ToolsmithCycleScheduler) -> None:
    """Stop a scheduler that is being abandoned, reporting a failed stop.

    Args:
        scheduler: The scheduler to shut down.
    """
    try:
        await scheduler.stop()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _toolsmith_disabled(
            "toolsmith scheduler cleanup failed after wiring error", exc
        )


async def wire_toolsmith(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    approval_store: ApprovalStoreProtocol | None,
    cost_tracker: CostTrackerProtocol | None,
) -> None:
    """Wire the self-extending toolkit at startup.

    Wired unconditionally whenever a provider is registered AND persistence
    is connected (authored blueprints are durable), so the
    ``tool_creation_enabled`` gate can flip on at runtime without a restart.
    The service is fail-safe when the gate is off: ``run_cycle`` no-ops and
    ``apply`` rejects on the live read, the allowlist is re-read per gap, and
    the existing ``meta.toolsmith_cycle_paused`` switch still pauses the
    scheduler. Idempotent for re-entered lifespans (shared-app fixtures).

    Raises:
        SubsystemDeclinedError: No provider registry or no persistence, so
            authoring has nothing to call and nowhere to store a blueprint.
    """
    from synthorg.meta.toolsmith.state import (  # noqa: PLC0415
        ToolsmithStateSlice,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
    )

    if app_state.slice(ToolsmithStateSlice).service is not None:
        return
    if provider_registry is None:
        msg = "no provider registry; authoring a tool is an LLM call"
        raise SubsystemDeclinedError(msg)
    if persistence is None or app_state.slice(PersistenceStateSlice).backend is None:
        msg = "no persistence backend; authored blueprints are durable"
        raise SubsystemDeclinedError(msg)
    from synthorg.meta.state import self_improvement_config_of  # noqa: PLC0415

    si_config = await self_improvement_config_of(app_state)
    try:
        runtime = _build_toolsmith_runtime(
            app_state=app_state,
            si_config=si_config,
            provider_registry=provider_registry,
            persistence=persistence,
            approval_store=approval_store,
            cost_tracker=cost_tracker,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _toolsmith_disabled("toolsmith runtime construction failed", exc)
        return
    if not _install_dynamic_layer(runtime):
        return
    if not await _start_cycle_scheduler(
        app_state, runtime=runtime, si_config=si_config
    ):
        return
    # Route every unfulfilled-capability MCP envelope into the service's gap
    # store so a recurring gap is observed. Installed only after the scheduler
    # is started and the state slice is published, so a failed start/swap
    # leaves no dangling sink routing envelopes into a store that nothing
    # drains (fail-closed).
    from synthorg.meta.mcp.server import (  # noqa: PLC0415
        install_capability_gap_sink,
    )

    install_capability_gap_sink(runtime.service)
    logger.info(API_APP_STARTUP, service="toolsmith", note="wired")
