"""Toolsmith (self-extending toolkit) construction + startup wiring.

Relocated out of the ``create_app`` body: the backend-specific authored-tool
repository builder, the toolsmith-runtime factory, and the startup step that
wires the toolsmith once a provider + connected persistence are present.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.tracker import CostTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.meta.config import SelfImprovementConfig
    from synthorg.meta.toolsmith.factory import ToolsmithRuntime
    from synthorg.meta.toolsmith.models import ToolBlueprint
    from synthorg.meta.toolsmith.protocol import GoldenScorecardProvider
    from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)


def _build_dynamic_tool_repo(
    persistence: PersistenceBackend,
) -> DynamicToolRepository:
    """Build the backend-specific authored-tool blueprint repository.

    Returns:
        The SQLite or Postgres dynamic-tool blueprint repository.
    """
    if persistence.backend_name == "sqlite":
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
    from synthorg.persistence.db_handle import postgres_pool  # noqa: PLC0415
    from synthorg.persistence.postgres.tool_blueprint_repo import (  # noqa: PLC0415
        PostgresDynamicToolRepository,
    )

    return PostgresDynamicToolRepository(postgres_pool(persistence))


def _build_toolsmith_runtime(  # noqa: PLR0913 -- explicit DI of the toolsmith runtime dependencies
    *,
    si_config: SelfImprovementConfig,
    provider_registry: ProviderRegistry,
    persistence: PersistenceBackend,
    approval_store: ApprovalStoreProtocol | None,
    cost_tracker: CostTracker | None,
    workspace_root: Path,
) -> ToolsmithRuntime | None:
    """Resolve dependencies and build the toolsmith runtime, or None.

    Returns ``None`` when no provider is registered (nothing to author
    with). The sandbox resolver maps each blueprint's declared backend to
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
        The built toolsmith runtime, or ``None`` when no provider is registered.
    """
    from synthorg.meta.toolsmith.factory import build_toolsmith  # noqa: PLC0415
    from synthorg.tools.sandbox.factory import (  # noqa: PLC0415
        build_sandbox_backends,
    )
    from synthorg.tools.sandbox.sandboxing_config import (  # noqa: PLC0415
        SandboxingConfig,
    )

    provider_names = provider_registry.list_providers()
    if not provider_names:
        return None
    provider = provider_registry.get(provider_names[0])
    repo = _build_dynamic_tool_repo(persistence)

    sandboxing = SandboxingConfig()
    backends = build_sandbox_backends(config=sandboxing, workspace=workspace_root)

    def _resolve_sandbox(blueprint: ToolBlueprint) -> SandboxBackend:
        return backends.get(
            blueprint.sandbox_backend.value, backends[sandboxing.default_backend]
        )

    scorecard_provider = _build_golden_scorecard_provider(
        si_config.toolsmith.validation.golden_scorecard_provider
    )

    return build_toolsmith(
        si_config=si_config,
        provider=provider,
        repo=repo,
        sandbox_resolver=_resolve_sandbox,
        scorecard_provider=scorecard_provider,
        approval_store=approval_store,
        cost_tracker=cost_tracker,
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


async def wire_toolsmith(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    approval_store: ApprovalStoreProtocol | None,
    cost_tracker: CostTracker | None,
) -> None:
    """Wire the self-extending toolkit at startup when enabled.

    Wired only when ``tool_creation_enabled`` is set AND a provider is
    registered AND persistence is connected (authored blueprints are
    durable). Disabled by default, so a normal boot skips this entirely.
    Idempotent for re-entered lifespans (shared-app fixtures).
    """
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )
    from synthorg.meta.toolsmith.state import (  # noqa: PLC0415
        ToolsmithStateSlice,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
    )
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if (
        app_state.slice(ToolsmithStateSlice).service is not None
        or provider_registry is None
    ):
        return
    if persistence is None or app_state.slice(PersistenceStateSlice).backend is None:
        return
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415

    si_config = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    if not si_config.tool_creation_enabled:
        return
    try:
        runtime = _build_toolsmith_runtime(
            si_config=si_config,
            provider_registry=provider_registry,
            persistence=persistence,
            approval_store=approval_store,
            cost_tracker=cost_tracker,
            workspace_root=agent_workspace_root_of(app_state),
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="toolsmith",
            note="toolsmith wiring failed; self-extending toolkit disabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    if runtime is None:
        return
    # Install the layered MCP surface BEFORE the once-only AppState
    # mutation. ``set_toolsmith_service`` cannot be replayed on
    # retry, so if the layer install fails after the AppState mutation
    # the runtime is left half-wired (service present, layer missing)
    # with no path back. Installing first means a failure here leaves
    # the toolsmith disabled cleanly, mirroring the upstream try/except.
    from synthorg.meta.mcp.server import (  # noqa: PLC0415
        install_dynamic_tool_layer,
    )

    try:
        install_dynamic_tool_layer(runtime.dynamic_registry)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="toolsmith",
            note="toolsmith dynamic layer install failed; disabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    # Route every unfulfilled-capability MCP envelope into the service's gap
    # store so a recurring gap is observed, then start the periodic detection
    # cycle so the org proposes new tools automatically rather than only on a
    # manual trigger.
    from synthorg.meta.mcp.server import (  # noqa: PLC0415
        install_capability_gap_sink,
    )
    from synthorg.meta.toolsmith.cycle_scheduler import (  # noqa: PLC0415
        ToolsmithCycleScheduler,
    )
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    scheduler = ToolsmithCycleScheduler(
        runtime.service,
        interval_seconds=si_config.toolsmith.cycle_interval_seconds,
        config_resolver=config_resolver_of(app_state),
    )
    try:
        await scheduler.start()
        app_state.swap_slice(
            ToolsmithStateSlice(service=runtime.service, cycle_scheduler=scheduler),
        )
    except Exception as exc:
        reraise_critical(exc)
        try:
            await scheduler.stop()
        except Exception as stop_exc:
            reraise_critical(stop_exc)
            logger.warning(
                API_APP_STARTUP,
                service="toolsmith",
                note="toolsmith scheduler cleanup failed after wiring error",
                error_type=type(stop_exc).__name__,
                error=safe_error_description(stop_exc),
            )
        logger.warning(
            API_APP_STARTUP,
            service="toolsmith",
            note="toolsmith scheduler wiring failed; self-extending toolkit disabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    # Install the gap sink only after the scheduler is started and the state
    # slice is published, so a failed start/swap leaves no dangling sink
    # routing envelopes into a store that nothing drains (fail-closed).
    install_capability_gap_sink(runtime.service)
    logger.info(API_APP_STARTUP, service="toolsmith", note="wired")
