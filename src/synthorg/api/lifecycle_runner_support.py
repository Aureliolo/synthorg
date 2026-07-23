# module-kind: code
"""Shared state + helpers for the on-startup / on-shutdown runners.

``_LifecycleTasks`` carries the mutable janitor-task / auto-wired-dispatcher /
health-prober handles that the startup runner sets and the shutdown runner
tears down (the historic ``nonlocal`` state of ``_build_lifecycle``'s closures).
The three wiring helpers are shared by both runners. Split into a leaf module so
the startup + shutdown runners and the builder import them without a cycle.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from synthorg.api.state import _ENTRY_TASK_DRAIN_GRACE_SECONDS, AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.client.state import client_simulation_state_of, has_simulation_runtime
from synthorg.communication.state import CommunicationStateSlice
from synthorg.integrations.chat_api.inbound.consumer import ChatInboundConsumer
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APP_SHUTDOWN,
    API_APP_SHUTDOWN_TIMEOUT,
    API_APP_STARTUP,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.health_prober import ProviderHealthProber
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)


async def drain_simulation_background_tasks(app_state: AppState) -> None:
    """Drain in-flight client-simulation pipeline tasks, bounded by grace.

    The intake-approval, simulation-runner, and task-board pipeline tasks are
    spawned fire-and-forget and tracked only on
    ``ClientSimulationState.background_tasks``. Give the live tasks a bounded
    grace to finish at a turn boundary, then cancel and await any straggler so
    it unwinds cleanly instead of being abandoned mid-write when the loop tears
    down. Snapshots the set up front because a completing task's done-callback
    discards itself from the live set. No-op when no simulation runtime is wired.
    """
    if not has_simulation_runtime(app_state):
        return
    sim_state = client_simulation_state_of(app_state)
    pending = {task for task in sim_state.background_tasks if not task.done()}
    if not pending:
        return
    done, still_running = await asyncio.wait(
        pending, timeout=_ENTRY_TASK_DRAIN_GRACE_SECONDS
    )
    for task in done:
        # ``task.exception()`` raises ``CancelledError`` on a cancelled task,
        # so guard with ``cancelled()`` first (a clean skip, not a raise).
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            logger.warning(
                API_APP_SHUTDOWN,
                service="simulation_task_drain",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="simulation task raised before shutdown drain deadline",
            )
    for task in still_running:
        task.cancel()
    if still_running:
        # ``shield`` so a cancel of this drain (the outer budget) does not
        # re-orphan the very stragglers it is awaiting. Mirror the ``done``
        # loop above: a straggler that raised a non-cancellation error during
        # its final unwind must not vanish silently.
        results = await asyncio.shield(
            asyncio.gather(*still_running, return_exceptions=True)
        )
        for result in results:
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.warning(
                    API_APP_SHUTDOWN,
                    service="simulation_task_drain",
                    error_type=type(result).__name__,
                    error=safe_error_description(result),
                    note="simulation task raised during shutdown drain",
                )


@dataclass
class _LifecycleTasks:
    """Mutable handles the startup runner sets and the shutdown runner clears.

    These were the ``nonlocal`` state of ``_build_lifecycle``'s closures; a
    shared container lets the extracted ``_run_startup`` / ``_run_shutdown``
    functions own the same state across the lifespan without a closure.
    """

    ticket_cleanup_task: asyncio.Task[None] | None = None
    audit_retention_task: asyncio.Task[None] | None = None
    flight_recorder_retention_task: asyncio.Task[None] | None = None
    webhook_cleanup_task: asyncio.Task[None] | None = None
    auto_wired_dispatcher: SettingsChangeDispatcher | None = None
    health_prober: ProviderHealthProber | None = None
    chat_inbound_consumer: ChatInboundConsumer | None = None


async def _cancel_with_timeout(
    task: asyncio.Task[None],
    *,
    service: str,
    timeout: float,  # noqa: ASYNC109 -- per-task shutdown budget
) -> None:
    """Cancel *task* and await completion with a hard timeout.

    The three janitor loops the lifecycle builder owns are wake-poll-sleep loops
    with no in-flight work, so a body that shields ``CancelledError`` is the
    only realistic way to hang here. Bound the wait to *timeout* and log at
    ERROR via ``API_APP_SHUTDOWN_TIMEOUT`` when the budget elapses; downstream
    service-teardown still runs.

    ``CancelledError`` is the normal completion path and is suppressed.
    ``MemoryError`` / ``RecursionError`` are re-raised because they must surface.

    Args:
        task: The janitor task to cancel.
        service: Service label for the structured shutdown log.
        timeout: Hard cancel-and-await budget (seconds).

    Raises:
        MemoryError: Re-raised unchanged.
        RecursionError: Re-raised unchanged.
    """
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.CancelledError:
        return
    except MemoryError, RecursionError:
        raise
    except TimeoutError as exc:
        log_exception_redacted(
            logger,
            API_APP_SHUTDOWN_TIMEOUT,
            exc,
            service=service,
            timeout_seconds=timeout,
            context=f"Failed to cancel {service} within shutdown budget",
        )
    except Exception as exc:  # noqa: BLE001 -- janitor best-effort: log and continue
        # A janitor task that fails with a non-timeout exception must not be
        # silently swallowed -- log at ERROR via the shutdown event so the
        # operator sees the underlying cause, then continue with downstream
        # services (the helper's contract is "never block the whole shutdown
        # window").
        log_exception_redacted(
            logger,
            API_APP_SHUTDOWN,
            exc,
            service=service,
            phase="task_cancellation",
            context=f"{service} task crashed during cancellation",
        )


async def _wire_workflow_observer(
    task_engine: object,
    persistence: PersistenceBackend,
    app_state: AppState,
) -> None:
    """Register WorkflowExecutionObserver on `task_engine` once.

    Must be called after the SettingsService auto-wire so the observer's
    underlying service resolves `max_subworkflow_depth` from the live
    `config_resolver`. Passes `config_resolver=None` (with an INFO log) when
    no resolver is wired; the service then falls back to the
    `EngineBridgeConfig` seed default. The observer never activates
    workflows (only forwards terminal task transitions), so the depth cap is
    immaterial here, but the resolver is threaded for forward-compatibility.
    """
    if task_engine is None or persistence is None:
        return
    if not (
        hasattr(persistence, "workflow_definitions")
        and hasattr(persistence, "workflow_executions")
    ):
        return
    from synthorg.engine.workflow.execution_observer import (  # noqa: PLC0415
        WorkflowExecutionObserver,
    )

    if any(
        isinstance(o, WorkflowExecutionObserver)
        for o in getattr(task_engine, "_observers", ())
    ):
        return
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    if config_resolver is None:
        logger.info(
            API_APP_STARTUP,
            component="workflow_execution_observer",
            note=(
                "config_resolver not wired; registering observer with the "
                "EngineBridgeConfig seed default for max_subworkflow_depth"
            ),
        )
    observer = WorkflowExecutionObserver(
        definition_repo=persistence.workflow_definitions,
        execution_repo=persistence.workflow_executions,
        task_engine=task_engine,  # type: ignore[arg-type]
        config_resolver=config_resolver,
    )
    task_engine.register_observer(observer)  # type: ignore[attr-defined]


def _wire_task_activity_observer(
    task_engine: object,
    persistence: PersistenceBackend,
    app_state: AppState,
    channels_plugin: object,
) -> None:
    """Register the task-activity observer on ``task_engine`` once.

    Publishes every persisted task transition to the ``tasks`` WS channel and
    records a terminal run's outcome as a task metric, so the dashboard Live
    Activity feed and org-health derive from real execution. Logs and skips
    when a prerequisite (task engine / channels plugin / performance tracker)
    is absent; the feed then simply lacks live task rows rather than the boot
    failing. Idempotent: never double-registers.
    """
    from litestar.channels import ChannelsPlugin  # noqa: PLC0415

    from synthorg.api.task_activity_observer import (  # noqa: PLC0415
        ActivityAgentRef,
        TaskActivityObserver,
    )
    from synthorg.core.artifact import Artifact  # noqa: PLC0415
    from synthorg.core.normalization import (  # noqa: PLC0415
        normalize_ascii_lowercase,
    )
    from synthorg.core.task import Task  # noqa: PLC0415
    from synthorg.engine.completion_oracle.evaluator import (  # noqa: PLC0415
        BuildTestOracle,
    )
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.persistence.artifact_protocol import (  # noqa: PLC0415
        ArtifactFilterSpec,
    )

    if task_engine is None or not isinstance(channels_plugin, ChannelsPlugin):
        logger.warning(
            API_APP_STARTUP,
            component="task_activity_observer",
            note=(
                "task engine or channels plugin absent; skipping observer "
                "wiring (dashboard live-activity feed lacks live task rows)"
            ),
        )
        return
    tracker = app_state.slice(HrStateSlice).performance_tracker
    if tracker is None:
        logger.warning(
            API_APP_STARTUP,
            component="task_activity_observer",
            note=(
                "performance tracker absent; skipping observer wiring "
                "(org health and live-activity task rows lack real data)"
            ),
        )
        return
    if task_engine.has_observer_type(TaskActivityObserver):  # type: ignore[attr-defined]
        return

    async def _list_artifacts(task_id: str) -> Sequence[Artifact]:
        return await persistence.artifacts.query(ArtifactFilterSpec(task_id=task_id))

    _build_test_oracle = BuildTestOracle()

    async def _oracle_block_for(task: Task) -> bool:
        # Re-source the run outcome for the live feed the same way the approvals
        # queue does, so a code task whose tests failed / never ran shows FAILED
        # on both surfaces rather than only in the queue.
        evaluation = await _build_test_oracle.evaluate(
            task, records=persistence.code_execution_records
        )
        return evaluation.blocks_completion

    async def _resolve_agent(agent_id: str) -> ActivityAgentRef | None:
        # Resolve the assignee's display identity at event time so a live
        # config change (renamed agent, moved department) is reflected without
        # reconstructing the observer. ``get_agents`` is config-resolver cached.
        agents = await config_resolver_of(app_state).get_agents()
        target = normalize_ascii_lowercase(agent_id)
        for agent in agents:
            if normalize_ascii_lowercase(str(agent.id)) == target:
                return ActivityAgentRef(
                    name=agent.name, role=agent.role, department=agent.department
                )
        return None

    observer = TaskActivityObserver(
        # ``wait_published`` (direct backend delivery), NOT ``publish`` (background
        # pub-queue): a transition can publish while the channels plugin is being
        # torn down, and the plugin's pub worker races its own shutdown; the
        # direct path has no such worker. See TaskActivityObserver.PublishFn.
        publish=channels_plugin.wait_published,
        list_artifacts=_list_artifacts,
        record_metric=tracker.record_task_metric,
        resolve_agent=_resolve_agent,
        oracle_block_for=_oracle_block_for,
    )
    task_engine.register_observer(observer)  # type: ignore[attr-defined]
    logger.info(API_SERVICE_AUTO_WIRED, service="task_activity_observer")


def _wire_workflow_execution_service(
    persistence: PersistenceBackend | None,
    app_state: AppState,
) -> None:
    """Wire the singleton ``WorkflowExecutionService`` onto ``EngineStateSlice``.

    Constructed once after persistence connects and the SettingsService
    auto-wire publishes ``config_resolver`` (the service resolves
    ``max_subworkflow_depth`` per ``activate`` so a live settings change is
    honoured without reconstruction). Idempotent; skips when already wired
    or a prerequisite (task engine / workflow repos / resolver) is absent,
    in which case the controller's 503 path
    (``workflow_execution_service_of``) fires, matching the previous
    per-request construction's ``config_resolver``-mandatory contract.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.engine.workflow.execution_service import (  # noqa: PLC0415
        WorkflowExecutionService,
    )

    if app_state.slice(EngineStateSlice).workflow_execution_service is not None:
        return
    task_engine = app_state.slice(EngineStateSlice).task_engine
    if task_engine is None or persistence is None:
        return
    if not (
        hasattr(persistence, "workflow_definitions")
        and hasattr(persistence, "workflow_executions")
    ):
        return
    config_resolver = app_state.slice(SettingsStateSlice).config_resolver
    if config_resolver is None:
        # Mirror the observer wirer's diagnostic: without the resolver the
        # service stays unwired and every /workflow-executions endpoint 503s,
        # so log the cause rather than leaving an operator to infer it from a
        # bare "service not wired" 503.
        logger.warning(
            API_APP_STARTUP,
            component="workflow_execution_service",
            note=(
                "config_resolver not wired; skipping WorkflowExecutionService "
                "wiring (workflow-execution endpoints will 503)"
            ),
        )
        return
    service = WorkflowExecutionService(
        definition_repo=persistence.workflow_definitions,
        execution_repo=persistence.workflow_executions,
        task_engine=task_engine,
        config_resolver=config_resolver,
    )
    app_state.wire_if_field_absent(
        EngineStateSlice, "workflow_execution_service", service
    )
    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="workflow_execution_service",
    )


def _wire_webhook_request_services(
    persistence: PersistenceBackend | None,
    app_state: AppState,
) -> None:
    """Wire the webhook activity service + replay protector onto the slice.

    Both are request-path singletons; wiring them once at startup avoids
    per-request construction under a lock and keeps the controller free
    of direct ``persistence.webhook_receipts`` access:

    * The activity service (read path) needs a connected persistence
      backend; skipped (controller 503s) when persistence is absent.
    * The replay protector's in-process nonce cache MUST be a single
      instance shared by every request, so a per-request build is wrong
      regardless of persistence; it is wired from
      ``integrations.webhooks.replay_window_seconds``.

    Idempotent: each field is wired only when absent, so a shared-app
    re-entry into lifespan does not discard the protector's seen-nonce
    cache.
    """
    from synthorg.api.controllers._webhooks_wiring import (  # noqa: PLC0415
        _REPLAY_PROTECTOR_MAX_ENTRIES,
    )
    from synthorg.integrations.state import IntegrationsStateSlice  # noqa: PLC0415
    from synthorg.integrations.webhooks.activity_service import (  # noqa: PLC0415
        WebhookActivityService,
    )
    from synthorg.integrations.webhooks.receipt_service import (  # noqa: PLC0415
        WebhookReceiptService,
    )
    from synthorg.integrations.webhooks.replay_protection import (  # noqa: PLC0415
        ReplayProtector,
    )

    slice_ = app_state.slice(IntegrationsStateSlice)
    if slice_.webhook_replay_protector is None:
        cfg = app_state.config.integrations.webhooks
        app_state.wire_if_field_absent(
            IntegrationsStateSlice,
            "webhook_replay_protector",
            ReplayProtector(
                window_seconds=cfg.replay_window_seconds,
                max_entries=_REPLAY_PROTECTOR_MAX_ENTRIES,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="webhook_replay_protector")
    if slice_.webhook_service is None:
        # The definition-CRUD facade backs the synthorg_webhooks_* MCP tools.
        # Its store is process-local (no durable definition store exists), so
        # it needs no persistence and wires unconditionally, like the replay
        # protector above.
        from synthorg.integrations.webhooks.definition_store import (  # noqa: PLC0415
            InMemoryWebhookDefinitionStore,
        )
        from synthorg.integrations.webhooks.service import (  # noqa: PLC0415
            WebhookService,
        )

        app_state.wire_if_field_absent(
            IntegrationsStateSlice,
            "webhook_service",
            WebhookService(
                store=InMemoryWebhookDefinitionStore(),
                clock=app_state.clock,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="webhook_service")
    if (
        slice_.webhook_activity_service is None
        and persistence is not None
        and getattr(persistence, "is_connected", False)
        and hasattr(persistence, "webhook_receipts")
    ):
        app_state.wire_if_field_absent(
            IntegrationsStateSlice,
            "webhook_activity_service",
            WebhookActivityService(receipts_repo=persistence.webhook_receipts),
        )
        logger.info(
            API_SERVICE_AUTO_WIRED,
            service="webhook_activity_service",
            backend=type(persistence).__name__,
        )
    if (
        slice_.webhook_receipt_service is None
        and persistence is not None
        and getattr(persistence, "is_connected", False)
        and hasattr(persistence, "webhook_receipts")
    ):
        app_state.wire_if_field_absent(
            IntegrationsStateSlice,
            "webhook_receipt_service",
            WebhookReceiptService(
                receipts_repo=persistence.webhook_receipts,
                clock=app_state.clock,
            ),
        )
        logger.info(
            API_SERVICE_AUTO_WIRED,
            service="webhook_receipt_service",
            backend=type(persistence).__name__,
        )


async def _wire_approval_gate(
    persistence: PersistenceBackend | None,
    app_state: AppState,
) -> None:
    """Construct the single boot ApprovalGate once persistence connects.

    One gate, shared by both governance sides: the engine parks blocked contexts
    and the ``/approvals`` controller resumes them. Park and resume must operate
    on the same gate over the same ``ParkedContextRepository``.

    Idempotent: a re-entered lifespan (shared-app test fixtures) skips when a
    gate is already wired. When persistence is absent or not connected the gate
    is still constructed (with no parked repo) so the single-gate invariant and
    the review-gate flow hold.
    """
    if app_state.slice(ApprovalStateSlice).gate is not None:
        return
    from synthorg.engine.approval_gate import ApprovalGate  # noqa: PLC0415
    from synthorg.engine.park_service import ParkService  # noqa: PLC0415

    parked_repo = None
    if (
        persistence is not None
        and getattr(persistence, "is_connected", False)
        and hasattr(persistence, "parked_contexts")
    ):
        parked_repo = persistence.parked_contexts
    # The boot gate bypasses the engine's _make_approval_gate(), so the
    # configured approval-interrupt timeout must be threaded in here explicitly
    # or any non-default setting is silently ignored. When the resolver is not
    # yet wired fall back to the EngineBridgeConfig seed default.
    if app_state.slice(SettingsStateSlice).config_resolver is not None:
        engine_bridge = await config_resolver_of(app_state).get_engine_bridge_config()
        interrupt_timeout = engine_bridge.approval_interrupt_timeout_seconds
    else:
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            EngineBridgeConfig,
        )

        interrupt_timeout = EngineBridgeConfig().approval_interrupt_timeout_seconds
    communication = app_state.slice(CommunicationStateSlice)
    gate = ApprovalGate(
        park_service=ParkService(),
        parked_context_repo=parked_repo,
        notification_dispatcher=app_state.slice(NotificationsStateSlice).dispatcher,
        event_hub=communication.event_stream_hub,
        interrupt_store=communication.interrupt_store,
        interrupt_timeout_seconds=interrupt_timeout,
    )
    app_state.swap_slice(
        app_state.slice(ApprovalStateSlice).model_copy(update={"gate": gate})
    )
    logger.info(
        API_SERVICE_AUTO_WIRED,
        service="approval_gate",
        has_parked_context_repo=parked_repo is not None,
    )
