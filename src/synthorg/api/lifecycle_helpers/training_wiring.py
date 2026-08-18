# module-kind: code
"""Subsystem activation for the training service.

Declared rather than auto-wired at startup. As a one-shot startup hook it
ran once, before the durable memory backend was guaranteed to exist, and any
failure inside it was swallowed into a ``severity=non_fatal`` warning; the
only visible consequence was ``eval_loop`` declining with "no training
service" for the life of the process, which names the symptom and not the
cause. A live boot did exactly that, on a ``TypeError`` from a defensive
deep copy, and the log line that held the real reason went unread.

As a subsystem it has its own name in ``GET /subsystems``, its own decline
reason, and the reconciler retries it on the pass where its dependencies
appear rather than never.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.state import ApprovalStateSlice
from synthorg.config.schema import RootConfig
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.tools.state import ToolsStateSlice, tool_invocation_tracker_of

logger = get_logger(__name__)


async def wire_training_service(
    app_state: AppState,
    effective_config: RootConfig | None,
) -> None:
    """Build and install the training service, or decline saying why.

    Args:
        app_state: Application state carrying every collaborator.
        effective_config: Root configuration; ``None`` on a boot that never
            resolved one.

    Raises:
        SubsystemDeclinedError: When a precondition this activation can name
            is absent. Every other failure propagates: a training service
            that could not be built for a reason nobody anticipated is a
            defect, and swallowing it is what hid the last one.
    """
    from synthorg._core.features import require_service  # noqa: PLC0415
    from synthorg.hr.training.factory import build_training_service  # noqa: PLC0415
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(HrStateSlice).training_service is not None:
        return
    if effective_config is None:
        msg = "no resolved configuration; training reads its policy from it"
        raise SubsystemDeclinedError(msg)
    if not effective_config.training.enabled:
        msg = "training is switched off (training.enabled)"
        raise SubsystemDeclinedError(msg)
    if app_state.slice(ToolsStateSlice).invocation_tracker is None:
        msg = "no tool-invocation tracker; extraction reads what agents did"
        raise SubsystemDeclinedError(msg)
    tracker = app_state.slice(HrStateSlice).performance_tracker
    if tracker is None:
        msg = "no performance tracker; source selection ranks from its records"
        raise SubsystemDeclinedError(msg)
    memory_backend = app_state.slice(MemoryStateSlice).backend
    if memory_backend is None:
        msg = "no memory backend; a new hire learns nothing that is not stored"
        raise SubsystemDeclinedError(msg)

    # Two different registries meet here and only one is optional. The AGENT
    # registry is structural: the source selectors read `list_active` and
    # `list_by_department` off it, so with no roster there is nobody to learn
    # from, which is why the spec `requires` it and this resolves it hard. The
    # PROVIDER registry below is the optional one: unset degrades `llm_curated`
    # to deterministic scoring rather than withholding the service.
    provider_registry = app_state.slice(ProvidersStateSlice).registry
    service = build_training_service(
        config=effective_config.training,
        memory_backend=memory_backend,
        tracker=tracker,
        registry=agent_registry_of(app_state),
        approval_store=require_service(
            app_state.slice(ApprovalStateSlice).store,
            "Approval Store",
        ),
        tool_tracker=tool_invocation_tracker_of(app_state),
        # The curator reads candidate training material and decides what a new
        # hire learns, so the connection it runs on is the operator's explicit
        # choice, re-read per curation call; unset degrades the ``llm_curated``
        # strategy to deterministic scoring.
        connections=None if provider_registry is None else provider_registry.get,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
    )
    app_state.wire(HrStateSlice, training_service=service)


async def unwire_training_service(app_state: AppState) -> None:
    """Drop the training service so the next pass rebuilds it.

    The extractors are built FROM the memory backend, so a replaced backend
    leaves this service reading through the instance the memory subsystem just
    disconnected, while still reporting itself up.

    Args:
        app_state: Application state carrying the HR slice.
    """
    app_state.wire(HrStateSlice, training_service=None)
    logger.info(API_APP_STARTUP, service="training_service", note="unwired")


__all__ = ["unwire_training_service", "wire_training_service"]
