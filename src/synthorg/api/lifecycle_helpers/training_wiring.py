"""On-startup auto-wire for the training service.

Deferred to startup rather than construction because it needs the durable
memory backend, which is only wired once persistence is connected. Every
other dependency (agent registry, tool-invocation tracker, performance
tracker) is already present from the construction phase.

Best-effort like the rest of the startup auto-wires: a failure leaves the
service unwired with a warning rather than aborting boot, because training
is an enhancement to hiring, not a precondition for the org running.
"""

from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.memory.state import MemoryStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.tools.state import ToolsStateSlice, tool_invocation_tracker_of

logger = get_logger(__name__)


def _should_wire(app_state: AppState, effective_config: RootConfig | None) -> bool:
    """Whether every precondition for building the service is met.

    Returns:
        ``True`` when training is enabled and nothing has wired the service
        yet, with both construction-phase dependencies present.
    """
    return (
        app_state.slice(HrStateSlice).training_service is None
        and effective_config is not None
        and effective_config.training.enabled
        and app_state.slice(HrStateSlice).agent_registry is not None
        and app_state.slice(ToolsStateSlice).invocation_tracker is not None
    )


async def try_wire_training_service(
    app_state: AppState,
    effective_config: RootConfig | None,
) -> None:
    """Wire the training service once its startup-phase deps exist."""
    if not _should_wire(app_state, effective_config) or effective_config is None:
        return
    try:
        await _wire(app_state, effective_config)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort startup auto-wire
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            phase="training_service_auto_wire",
            severity="non_fatal",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire(app_state: AppState, effective_config: RootConfig) -> None:
    """Build and install the training service."""
    from synthorg._core.features import require_service  # noqa: PLC0415
    from synthorg.hr.training.factory import build_training_service  # noqa: PLC0415
    from synthorg.providers.model_binding import (  # noqa: PLC0415
        resolve_bound_completion,
    )

    tracker = app_state.slice(HrStateSlice).performance_tracker
    memory_backend = app_state.slice(MemoryStateSlice).backend
    if tracker is None or memory_backend is None:
        return
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
        # choice; unset degrades the ``llm_curated`` strategy to deterministic
        # scoring.
        curation_binding=await resolve_bound_completion(
            app_state,
            namespace="hr",
            key="training_curation_model",
            unset_event=API_APP_STARTUP,
            subject="training curation",
        ),
    )
    app_state.wire(HrStateSlice, training_service=service)


__all__ = ["try_wire_training_service"]
