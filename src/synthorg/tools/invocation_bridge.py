"""Bridge between ToolInvoker and invocation tracking.

Separates activity-tracking concerns from tool execution logic in
``invoker.py``.  Best-effort: failures here never affect tool results.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.tool import TOOL_INVOCATION_RECORD_FAILED
from synthorg.observability.metrics_hub import (
    record_tool_invocation as record_tool_invocation_metric,
)
from synthorg.tools.invocation_record import ToolInvocationRecord

if TYPE_CHECKING:
    from synthorg.providers.models import ToolCall, ToolResult
    from synthorg.tools.invoker import ToolInvoker

logger = get_logger(__name__)


async def record_tool_invocation(
    invoker: ToolInvoker,
    tool_call: ToolCall,
    result: ToolResult,
    *,
    started_at: datetime,
) -> None:
    """Record a tool invocation for activity tracking and Prometheus.

    Order-of-record contract:

    1. Write the activity record first. If the activity DB is
       unavailable, log a WARN at ``TOOL_INVOCATION_RECORD_FAILED``
       and continue: the Prometheus metric still emits so duration
       histograms aren't blind to a tracker outage.
    2. Then increment the Prometheus counter / histogram.

    The previous ordering (Prometheus first, activity DB second) was
    asymmetric: a tracker failure left a phantom metric sample with
    no audit row to correlate. Emitting the metric AFTER the
    activity-DB attempt means a high metric counter paired with a
    visible WARN about activity-DB failures is the unambiguous
    signature of an outage; both succeed-or-both-fail is the goal.

    Args:
        invoker: The invoker instance (provides agent/task context).
        tool_call: The tool call that was executed.
        result: The tool result.
        started_at: Wall-clock timestamp captured before the tool ran;
            used to compute the duration histogram observation.
    """
    completed_at = datetime.now(UTC)
    duration_sec = max(0.0, (completed_at - started_at).total_seconds())

    tracker = invoker._invocation_tracker  # noqa: SLF001
    agent_id = invoker._agent_id  # noqa: SLF001
    if tracker is not None and agent_id is not None:
        try:
            record = ToolInvocationRecord(
                agent_id=agent_id,
                task_id=invoker._task_id,  # noqa: SLF001
                tool_name=tool_call.name,
                is_success=not result.is_error,
                timestamp=completed_at,
                error_message=(result.content[:2048] if result.is_error else None),
            )
            await tracker.record(record)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                TOOL_INVOCATION_RECORD_FAILED,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                exc_info=True,
            )

    if result.is_timeout:
        outcome = "timeout"
    elif result.is_error:
        outcome = "error"
    else:
        outcome = "success"
    # Best-effort: the activity-DB record above is the durable
    # source of truth, and the tool's caller has already received
    # the ``result``. A label-validation TypeError or transient
    # collector exception in the metric path must NOT bubble up and
    # mask the completed invocation. ``record_tool_invocation_metric``
    # already routes through ``_safe_record`` for most exceptions,
    # but TypeError propagates by design; this guard makes the
    # bridge level fully best-effort.
    try:
        record_tool_invocation_metric(
            tool_name=tool_call.name,
            outcome=outcome,
            duration_sec=duration_sec,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            TOOL_INVOCATION_RECORD_FAILED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            stage="prometheus_metric",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
