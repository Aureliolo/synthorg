"""Module-level startup steps for the Litestar ``on_startup`` sequence.

Each step takes its dependencies explicitly (rather than closing over
``create_app`` locals) so the composition root stays a thin caller that
schedules them into the ``on_startup`` sequence.
"""

from pathlib import Path

from pydantic import ValidationError

from synthorg.api._app_wiring import (
    _try_wire_cockpit,
    _try_wire_cost_dial,
    _try_wire_environment_service,
    _try_wire_performance_persistence,
)
from synthorg.api._benchmark_wiring import seed_benchmark_scores
from synthorg.api.lifecycle_helpers.budget_wiring import hydrate_cost_window
from synthorg.api.lifecycle_helpers.durability_wiring import (
    _try_wire_audit_chain_persistence,
)
from synthorg.api.middleware import set_docs_csp_origins
from synthorg.api.state import AppState
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.error_taxonomy import set_error_docs_base_url
from synthorg.core.types import NotBlankStr
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.review_gate_inputs import AutonomyProvider
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_BRIDGE_CONFIG_RESOLVE_FAILED,
)
from synthorg.observability.events.red_team import RED_TEAM_GATE_SKIPPED
from synthorg.observability.events.settings import (
    SETTINGS_FETCH_FAILED,
    SETTINGS_VALUE_RESOLVED,
)
from synthorg.security.redteam.builder import RedTeamRuntime
from synthorg.settings.errors import SettingNotFoundError, SettingsEncryptionError

logger = get_logger(__name__)


def _publish_red_team_runtime(
    app_state: AppState,
    *,
    red_team_runtime: RedTeamRuntime | None,
    review_gate_service: ReviewGateService | None,
) -> None:
    """Publish or clear the red-team report store, then attach the gate.

    Publishes the per-execution red-team report store onto
    ``SecurityStateSlice`` so the deliverable-receipt builder (the
    ``deliverable_receipts`` subsystem, which comes up once the docs engine
    does) can snapshot a run's findings into its receipt. The publish is a
    partial wire, so the audit log / trust service /
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


def _try_wire_ssrf_violation_recorder(app_state: AppState) -> None:
    """Install the fail-safe SSRF-violation recorder when persistence is up.

    Gives the outbound SSRF guard a live audit trail: every blocked URL is
    recorded as a PENDING ``SsrfViolation`` for operator review via
    ``/providers/ssrf-violations``. A persistence-less boot (dev / test
    fixtures) clears the recorder so the chokepoint no-ops. Recording is
    best-effort and never weakens a block.
    """
    from synthorg.api.services.ssrf_violation_service import (  # noqa: PLC0415
        SsrfViolationService,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.providers.url_utils import redact_url  # noqa: PLC0415
    from synthorg.security.ssrf_violation import SsrfViolation  # noqa: PLC0415
    from synthorg.tools._ssrf_recording import (  # noqa: PLC0415
        install_ssrf_violation_recorder,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        install_ssrf_violation_recorder(None)
        return
    service = SsrfViolationService(repo=persistence_of(app_state).ssrf_violations)
    clock = app_state.clock

    async def _record(
        url: str,
        hostname: str,
        port: int,
        resolved_ip: str | None,
        blocked_range: str | None,
    ) -> None:
        await service.record(
            SsrfViolation(
                timestamp=clock.now(),
                url=NotBlankStr(redact_url(url)),
                hostname=NotBlankStr(hostname),
                port=port,
                resolved_ip=NotBlankStr(resolved_ip) if resolved_ip else None,
                blocked_range=(NotBlankStr(blocked_range) if blocked_range else None),
            )
        )

    install_ssrf_violation_recorder(_record)


async def install_runtime_services(
    app_state: AppState,
    *,
    connection_catalog: ConnectionCatalog | None,
    agent_workspace_root: Path | None,
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
    # the runtime data, not a process temp dir. Resolved once at the
    # composition root (bootstrap) and injected; injected/dev apps pass
    # ``None`` and keep the documented temp fallback.
    if agent_workspace_root is not None:
        app_state.wire(WorkspaceStateSlice, agent_workspace_root=agent_workspace_root)

    # Per-project persistent workspace substrate. The git backend is
    # config-selected (embedded default, no external dep);
    # ProjectWorkspaceService provisions one persistent git-backed
    # tree per project under the workspace base. Persistence-less
    # boots (test fixtures, dev apps with no DB) skip wiring.
    _try_wire_cost_dial(app_state)
    # The spend window is in memory and starts empty, so without this a
    # restart reads as an org that has spent nothing: every summary reports
    # zero and every ceiling starts over. Runs after the cost dial, which is
    # what attaches the durable record store it reads from.
    await hydrate_cost_window(app_state)
    _try_wire_ssrf_violation_recorder(app_state)
    # Attach durable metric repos to the performance tracker now that the
    # backend is connected; a restart otherwise discards all recorded
    # task/collaboration performance metrics.
    _try_wire_performance_persistence(app_state)
    # Make the audit hash chain durable: hydrate from storage + drain new
    # appends; a restart otherwise loses the tamper-evident chain.
    await _try_wire_audit_chain_persistence(app_state)
    # Seed the measured benchmark-score repo from the committed artifact
    # (idempotent; measured arm only) now the cost-dial repo is wired.
    await seed_benchmark_scores(app_state)
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
    # keep this hook under the cyclomatic-complexity cap). Best-effort:
    # a misconfigured environment must 503 its controllers, not poison
    # the whole startup.
    _try_wire_environment_service(app_state)

    # Re-resolve the client-simulation runtime from the settings DB BEFORE
    # building the runtime services: ``build_runtime_services`` assembles the
    # coordinator, which captures the simulation intake/review components during
    # construction. Construction read only env/default, so a DB override of
    # intake_strategy / model / project / review pipeline must be applied here
    # first or the coordinator would pin the pre-override components while the
    # entry adapters read the newly swapped state.
    from synthorg.client.runtime_builder import (  # noqa: PLC0415
        reload_client_simulation_runtime,
    )
    from synthorg.client.state import has_simulation_runtime  # noqa: PLC0415

    if has_simulation_runtime(app_state):
        await reload_client_simulation_runtime(app_state)

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
    # wiring once the boot engine exists, published + attached here so the
    # deliverable-receipt builder can snapshot findings and the gate is
    # attached to the review service. ``None`` clears it on the
    # disabled-reinit path.
    _publish_red_team_runtime(
        app_state,
        red_team_runtime=services.red_team_runtime,
        review_gate_service=review_gate_service,
    )
    # The completion oracle: the deterministic build/test gate (blocks a failing
    # / unverified code task, attached whenever enabled since it needs no
    # provider) and the provider-backed peer-review gate. They fire on every path
    # to COMPLETED (auto-review -- on by default -- and human approve). The shared
    # seam re-resolves the review gate and clears each gate on the disabled /
    # no-provider path, so boot and hot-reload cannot diverge.
    from synthorg.workers._completion_oracle_runtime import (  # noqa: PLC0415
        attach_completion_oracle_gates,
    )

    attach_completion_oracle_gates(
        app_state,
        enabled=services.completion_oracle_enabled,
        completion_oracle_runtime=services.completion_oracle_runtime,
    )
    # Wire the shared deliverable-input builder so the completion-oracle
    # peer-review gate (on by default) and the red-team gate (opt-in) both have
    # a deliverable to review. This is independent of the red-team subsystem:
    # coupling it there left the on-by-default oracle reviewer with a ``None``
    # input, silently passing every task. No-op without persistence.
    if review_gate_service is not None:
        _wire_deliverable_input_builder(app_state, review_gate_service)
    # Red-team-specific completion extras: the on_missing_deliverable posture
    # and the background registry that keeps the inline red-team AgentEngine
    # latency off the approve/reject response. Only when the subsystem is on.
    if services.red_team_runtime is not None and review_gate_service is not None:
        _wire_red_team_completion(
            app_state,
            review_gate_service,
            services.red_team_runtime,
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

    # The client-simulation runtime was re-resolved from the settings DB before
    # the runtime services were built (above), so the coordinator captured the
    # DB-backed intake/review components; attach the real work-entry adapters.
    await wire_real_intake_entry(app_state)
    await wire_real_objective_entry(app_state)
    await wire_real_task_board_entry(app_state)


def _wire_deliverable_input_builder(
    app_state: AppState,
    review_gate_service: ReviewGateService,
) -> None:
    """Attach the shared deliverable-input builder (both completion gates).

    The completion-oracle peer-review gate (on by default) and the red-team
    gate (opt-in) both source the completing task's deliverable text +
    execution id from this one builder, which reads the flight-recorder frame
    store. Wired whenever persistence is connected, independently of either
    gate being enabled, so the on-by-default oracle reviewer always has a
    deliverable rather than a ``None`` input that silently passes the task.

    No-op when persistence is not connected: without a backend the
    flight-recorder deliverable source is unavailable, so a configured gate
    stays inert this boot (see ``run_completion_gates``) rather than crashing.
    """
    from synthorg.engine.artifacts.deliverable_content import (  # noqa: PLC0415
        workspace_deliverable_reader,
    )
    from synthorg.engine.review_gate_inputs import (  # noqa: PLC0415
        DeliverableReviewInputBuilder,
    )
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(PersistenceStateSlice).backend is None:
        logger.warning(
            API_APP_STARTUP,
            service="deliverable_input_builder",
            note=(
                "Deliverable-input builder wiring skipped: persistence is not "
                "connected, so the flight-recorder deliverable source is "
                "unavailable; the completion gates stay inert this boot."
            ),
        )
        return
    review_gate_service.set_deliverable_input_builder(
        DeliverableReviewInputBuilder(
            frame_repository=persistence_of(app_state).flight_recorder_frames,
            autonomy_provider=_company_autonomy_provider(app_state),
            # The reviewer judges the files the task promised. Bound to the
            # same root the agent's file tools write through, so it reads
            # what the run actually produced.
            deliverable_reader=workspace_deliverable_reader(
                agent_workspace_root_of(app_state),
                config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
            ),
        ),
    )


def _wire_red_team_completion(
    app_state: AppState,
    review_gate_service: ReviewGateService,
    runtime: RedTeamRuntime,
) -> None:
    """Attach the red-team posture + background registry.

    The shared deliverable-input builder is wired separately by
    ``_wire_deliverable_input_builder``; this attaches only the red-team
    specifics: the ``on_missing_deliverable`` posture, the routing-shared
    stakes threshold, and the background registry that keeps the inline
    red-team AgentEngine evaluation off the operator's approve/reject response.

    No-op when persistence is not connected: without a backend the deliverable
    source is unavailable, so the gate stays inert this boot rather than
    crashing on the missing frame repository.
    """
    # Share the routing layer's stakes threshold so the gate fires on exactly
    # the work the router marks red_team_required. Set before the persistence
    # guard: the threshold is independent of the deliverable source.
    review_gate_service.set_red_team_min_stakes(
        app_state.config.stakes_routing.red_team_min_stakes,
    )
    from synthorg.observability.background_tasks import (  # noqa: PLC0415
        BackgroundTaskRegistry,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        logger.warning(
            RED_TEAM_GATE_SKIPPED,
            reason="no_persistence",
            note=(
                "Red-team completion wiring skipped: persistence is not "
                "connected, so the flight-recorder deliverable source is "
                "unavailable; the gate stays inert this boot."
            ),
        )
        return
    review_gate_service.set_red_team_on_missing_deliverable(
        runtime.on_missing_deliverable,
    )
    review_gate_service.set_background_tasks(
        BackgroundTaskRegistry(owner="review_gate.completion"),
    )


def _company_autonomy_provider(app_state: AppState) -> AutonomyProvider:
    """Build an autonomy provider reading the company autonomy level.

    Returns:
        An async callable returning the configured company
        ``AutonomyLevel``, defaulting to the strict ``SUPERVISED``
        posture when settings are unavailable, unset, or unreadable so the
        red-team severity routing never silently relaxes.
    """

    async def _provide() -> AutonomyLevel:
        from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

        resolver = app_state.slice(SettingsStateSlice).config_resolver
        if resolver is None:
            return AutonomyLevel.SUPERVISED
        try:
            return await resolver.get_autonomy_level()
        except SettingNotFoundError:
            return AutonomyLevel.SUPERVISED
        except (SettingsEncryptionError, ValueError) as exc:
            # A stored autonomy_level that cannot be decrypted or is not a
            # valid enum member is a real misconfiguration: log it (unlike
            # the never-set case) and fall back to the strict posture so a
            # bad setting cannot crash every completion at the gate.
            logger.warning(
                SETTINGS_FETCH_FAILED,
                setting="autonomy_level",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="autonomy level unreadable; falling back to SUPERVISED",
            )
            return AutonomyLevel.SUPERVISED

    return _provide


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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
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
