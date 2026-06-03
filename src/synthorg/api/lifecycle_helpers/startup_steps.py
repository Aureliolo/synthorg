"""Module-level startup steps extracted from ``create_app``.

Relocated out of the ``create_app`` body so the composition root stays a
thin caller. Each step takes its dependencies explicitly (rather than
closing over ``create_app`` locals) and is scheduled into the Litestar
``on_startup`` sequence by the composition root.
"""

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from synthorg.api._app_wiring import (
    _try_wire_cockpit,
    _try_wire_cost_dial,
    _wire_environment_service,
)
from synthorg.api.app_helpers import resolve_agent_workspace_root_env
from synthorg.api.middleware import set_docs_csp_origins
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.error_taxonomy import set_error_docs_base_url
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_BRIDGE_CONFIG_RESOLVE_FAILED,
)
from synthorg.observability.events.settings import SETTINGS_VALUE_RESOLVED
from synthorg.settings.errors import SettingNotFoundError, SettingsEncryptionError

if TYPE_CHECKING:
    from synthorg.engine.review_gate import ReviewGateService
    from synthorg.security.redteam.builder import RedTeamRuntime

logger = get_logger(__name__)


def _publish_red_team_runtime(
    app_state: AppState,
    *,
    red_team_runtime: RedTeamRuntime | None,
    review_gate_service: ReviewGateService | None,
) -> None:
    """Publish or clear the red-team report store, then attach the gate.

    Publishes the per-execution red-team report store onto
    ``SecurityStateSlice`` so the deliverable-receipt builder (wired later in
    ``wire_features_on_startup``) can snapshot a run's findings into its
    receipt. The publish is a partial wire, so the audit log / trust service /
    autonomy strategy already on the slice survive. ``post_setup_reinit()``
    rebuilds runtime services on the existing app state, so an enabled ->
    disabled transition must reset the store to ``None``; otherwise the
    previous run's repository would keep leaking stale findings into fresh
    receipts. Independent of the review gate: receipts need the store even
    where no review gate is wired.

    Args:
        app_state: Application state holding the security slice.
        red_team_runtime: The built red-team bundle, or ``None`` when the
            adversarial subsystem is disabled.
        review_gate_service: The review-gate service to attach the red-team
            gate to, or ``None`` when no review gate is wired.
    """
    from synthorg.security.state import SecurityStateSlice  # noqa: PLC0415

    app_state.wire(
        SecurityStateSlice,
        red_team_reports=(
            red_team_runtime.report_repo if red_team_runtime is not None else None
        ),
    )
    # Attach the live gate, or clear it on the disabled path: a reinit that
    # turns red-team off must detach the previous run's gate so the review
    # pipeline does not keep firing a stale one.
    if review_gate_service is not None:
        review_gate_service.set_red_team_gate(
            red_team_runtime.gate if red_team_runtime is not None else None
        )


async def install_runtime_services(
    app_state: AppState,
    *,
    connection_catalog: Any,
) -> None:
    """Install worker-execution + coordinator runtime services at boot.

    Installs the worker execution service AND the multi-agent coordinator
    behind the single provider-present switch, both sharing one boot
    AgentEngine. Scheduled first (immediately after the core startup hooks
    that connect persistence and wire SettingsService / ConfigResolver) and
    before any other appended hook, so the once-only
    ``set_worker_execution_service`` / ``set_coordinator`` cannot lose a
    race with the worker-service property's lazy lifecycle-only default.
    With no provider this installs the empty-company backstop and no
    coordinator (``/coordinate`` honestly 503s); a provider added later
    swaps both in via ``post_setup_reinit`` (no restart). The caller guards
    re-entry idempotency so the one-shot ``set_`` calls survive a lifespan
    re-entry (shared-app test fixtures).

    Raises:
        RuntimeServicesBuildError: If the runtime-services build fails.
    """
    from synthorg.engine.errors import (  # noqa: PLC0415
        RuntimeServicesBuildError,
    )
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        WorkspaceStateSlice,
        agent_workspace_root_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.providers.state import (  # noqa: PLC0415
        has_active_provider,
    )
    from synthorg.workers.runtime_builder import (  # noqa: PLC0415
        build_runtime_services,
    )

    # Pin the sandbox workspace onto the mounted data volume in an
    # env-driven deployment so agent file/sandbox tools persist with
    # the runtime data, not a process temp dir. Injected/dev apps
    # return None and keep the documented temp fallback.
    env_workspace_root = resolve_agent_workspace_root_env()
    if env_workspace_root is not None:
        app_state.wire(WorkspaceStateSlice, agent_workspace_root=env_workspace_root)

    # Per-project persistent workspace substrate. The git backend is
    # config-selected (embedded default, no external dep);
    # ProjectWorkspaceService provisions one persistent git-backed
    # tree per project under the workspace base. Persistence-less
    # boots (test fixtures, dev apps with no DB) skip wiring.
    _try_wire_cost_dial(app_state)
    _try_wire_cockpit(app_state)

    # service is optional and gates on ``has_project_workspace_service``.
    if (
        app_state.slice(PersistenceStateSlice).backend is not None
        and app_state.slice(WorkspaceStateSlice).project_workspace_service is None
    ):
        # Guard against partial-startup retry: this hook fires once
        # the persistence layer is connected, but ``build_runtime_services``
        # below is fallible and a re-entry after its failure would
        # otherwise hit the ``_set_once`` guard inside
        # ``set_project_workspace_service`` and fail with
        # "already configured" instead of cleanly retrying the
        # runtime-services build.
        from synthorg.engine.workspace.git_backend import (  # noqa: PLC0415
            GitBackendConfig,
            GitBackendDeps,
            build_git_backend,
        )
        from synthorg.engine.workspace.project_workspace_service import (  # noqa: PLC0415
            ProjectWorkspaceService,
        )

        git_backend_config = GitBackendConfig()
        git_backend = build_git_backend(
            git_backend_config,
            GitBackendDeps(
                workspace_base_root=agent_workspace_root_of(app_state),
                connection_catalog=connection_catalog,
                clock=app_state.clock,
            ),
        )
        app_state.wire(
            WorkspaceStateSlice,
            project_workspace_service=ProjectWorkspaceService(
                base_root=agent_workspace_root_of(app_state),
                repo=persistence_of(app_state).project_workspaces,
                git_backend=git_backend,
                config=git_backend_config,
                clock=app_state.clock,
            ),
        )

    # Per-project reproducible environment substrate (extracted to
    # keep this hook under the cyclomatic-complexity cap).
    _wire_environment_service(app_state)

    try:
        services = await build_runtime_services(
            app_state,
            workspace_root=agent_workspace_root_of(app_state),
        )
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="runtime_services",
            note="failed to build the runtime services at boot",
            provider_present=has_active_provider(app_state),
        )
        msg = "Runtime services failed to build at boot"
        raise RuntimeServicesBuildError(msg) from exc
    app_state.set_worker_execution_service(
        services.worker_execution_service,
    )
    # An explicitly injected coordinator (``create_app(coordinator=)``
    # in tests / custom DI) wins over the autowired one, matching the
    # injection-over-autowire convention used across ``create_app``.
    # ``set_coordinator_if_absent`` makes the check-and-set atomic in
    # the seam (no boot-time check-then-act), so an injected
    # coordinator is kept and the built one is a logged no-op then.
    if services.coordinator is not None:
        app_state.set_coordinator_if_absent(services.coordinator)
    # Same injection-over-autowire rule for the work pipeline spine:
    # an injected ``create_app(work_pipeline=)`` is kept, the built
    # one is a logged no-op then.
    if services.work_pipeline is not None:
        app_state.set_work_pipeline_if_absent(services.work_pipeline)
    # Attach the vision verifier gate to the review gate service, or clear
    # it on the disabled path. The service was built during app construction
    # (before a provider connected); the gate is built here once the
    # workspace + provider are available. A reinit that turns vision off must
    # detach the previous gate so the review pipeline does not keep firing a
    # stale one (same enabled -> disabled concern as the red-team gate).
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415

    review_gate_service = app_state.slice(ApprovalStateSlice).review_gate
    if review_gate_service is not None:
        review_gate_service.set_vision_gate(services.vision_gate)
    # Same seam for the adversarial red-team gate: built in the runtime
    # wiring once the boot engine exists, published + attached here so a
    # review pipeline supplied with red_team_input reaches the live gate.
    _publish_red_team_runtime(
        app_state,
        red_team_runtime=services.red_team_runtime,
        review_gate_service=review_gate_service,
    )
    # Bring the real client-request, goal/objective, and
    # task-board work-entry paths online: ensure the configured
    # default projects exist and attach the entry adapters. No-op
    # for an empty company (no pipeline). The task-board adapter
    # follows the same gate but skips the project bootstrap (board
    # filings carry their own project).
    from synthorg.engine.pipeline.entry.boot import (  # noqa: PLC0415
        wire_real_intake_entry,
        wire_real_objective_entry,
        wire_real_task_board_entry,
    )

    await wire_real_intake_entry(app_state)
    await wire_real_objective_entry(app_state)
    await wire_real_task_board_entry(app_state)


async def wire_brownfield_intake(app_state: AppState) -> bool:
    """Wire the brownfield codebase-intake entry (best-effort, idempotent).

    Brownfield codebase intake (the "merger/acquisition" entry mode). Runs
    AFTER the knowledge engine is wired so the import service can index the
    codebase into the knowledge store. Best-effort: a missing collaborator
    (no persistence / workspace / knowledge) leaves the ``/brownfield``
    controller to 503 rather than poisoning startup. The caller guards
    re-entry idempotency off the returned flag.

    Returns:
        ``True`` when the intake entry was wired, ``False`` when a missing
        collaborator left it unavailable.
    """
    from synthorg.engine.pipeline.entry.boot import (  # noqa: PLC0415
        wire_real_brownfield_entry,
    )

    try:
        await wire_real_brownfield_entry(app_state)
    except Exception as exc:
        reraise_critical(exc)
        logger.info(
            API_APP_STARTUP,
            service="brownfield_intake",
            note="brownfield intake wiring unavailable; skipped",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
    return True


async def resolve_runtime_security_settings(app_state: AppState) -> None:
    """Resolve operator-overridable API security settings into module state.

    Each security key resolves independently so a validation failure on an
    unrelated ``api.*`` field (e.g. a bad ``request_max_body_size_bytes``)
    does not silently suppress CSP-origin or error-docs overrides. The
    shared ``ApiBridgeConfig`` validator still runs per key by constructing
    a one-field model -- defaults satisfy the remaining fields without
    re-resolving them. Failure branches actively re-write the module global
    to ``ApiBridgeConfig()`` defaults, not just log "fallback": a previous
    app instance (or earlier test on the same worker) may have already
    mutated the global, in which case skipping the write would silently
    keep a stale override instead of the documented default.
    """
    from synthorg.settings.bridge_configs import (  # noqa: PLC0415
        ApiBridgeConfig,
    )
    from synthorg.settings.state import (  # noqa: PLC0415
        SettingsStateSlice,
        config_resolver_of,
    )

    defaults = ApiBridgeConfig()

    if app_state.slice(SettingsStateSlice).config_resolver is None:
        set_docs_csp_origins(defaults.csp_docs_external_origins)
        set_error_docs_base_url(defaults.error_docs_base_url)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge="api",
            reason="config_resolver_unavailable",
            fallback="module_defaults",
        )
        return
    resolver = config_resolver_of(app_state)

    try:
        origins_raw = await resolver.get_json("api", "csp_docs_external_origins")
        # Pass the raw JSON shape directly so ApiBridgeConfig sees
        # the unmodified payload. ``tuple(...)`` would coerce a
        # mapping to its keys (and other non-iterable shapes to
        # TypeError), masking the real validation failure. Pydantic
        # returns a ``tuple[str, ...]`` after its own validation
        # runs, so ``set_docs_csp_origins`` still receives the
        # correct shape.
        csp_bridge = ApiBridgeConfig(csp_docs_external_origins=origins_raw)
        set_docs_csp_origins(csp_bridge.csp_docs_external_origins)
    except (
        SettingNotFoundError,
        SettingsEncryptionError,
        ValueError,
        ValidationError,
    ) as exc:
        set_docs_csp_origins(defaults.csp_docs_external_origins)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge="api",
            key="csp_docs_external_origins",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback="module_default",
        )

    try:
        url_raw = await resolver.get_str("api", "error_docs_base_url")
        error_bridge = ApiBridgeConfig(error_docs_base_url=url_raw)
        set_error_docs_base_url(error_bridge.error_docs_base_url)
        logger.info(
            SETTINGS_VALUE_RESOLVED,
            namespace="api",
            key="error_docs_base_url",
            value=error_bridge.error_docs_base_url,
        )
    except (
        SettingNotFoundError,
        SettingsEncryptionError,
        ValueError,
        ValidationError,
    ) as exc:
        set_error_docs_base_url(defaults.error_docs_base_url)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge="api",
            key="error_docs_base_url",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback="module_default",
        )
