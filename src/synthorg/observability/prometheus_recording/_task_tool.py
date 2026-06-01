# module-kind: code
"""Task-run and tool-invocation recording."""

from synthorg.observability import get_logger
from synthorg.observability.prometheus_labels import (
    VALID_TASK_OUTCOMES,
    VALID_TOOL_OUTCOMES,
    require_label,
    require_non_negative,
    validate_tool_name,
)
from synthorg.observability.prometheus_recording._base import (
    _RecordingMetricsBase,
)

logger = get_logger(__name__)


class _TaskToolRecordingMixin(_RecordingMetricsBase):
    """Task-run and tool-invocation recording."""

    def record_task_run(
        self,
        *,
        outcome: str,
        duration_sec: float | None,
    ) -> None:
        """Record a task's final outcome and runtime.

        Args:
            outcome: One of ``"succeeded"``, ``"failed"``,
                ``"cancelled"``, ``"rejected"``.
            duration_sec: Wall-clock duration in seconds, or
                ``None`` if the engine has no recorded creation
                timestamp (e.g. a task created before the current
                process restart). The outcome counter increments
                in either case; the duration histogram observation
                is skipped when ``duration_sec is None`` so an
                untimed task does not skew the distribution with a
                spurious 0-duration sample.

        Raises:
            ValueError: If *outcome* is not a valid value or
                ``duration_sec`` is negative.
        """
        require_label("task outcome", outcome, VALID_TASK_OUTCOMES)
        self._task_runs.labels(outcome=outcome).inc()
        if duration_sec is not None:
            require_non_negative("record_task_run: duration_sec", duration_sec)
            self._task_duration.labels(outcome=outcome).observe(duration_sec)

    def record_tool_invocation(
        self,
        *,
        tool_name: str,
        outcome: str,
        duration_sec: float,
    ) -> None:
        """Record a tool invocation's outcome and runtime.

        Args:
            tool_name: Registered tool name (e.g. ``"web_search"``).
            outcome: One of ``"success"``, ``"error"``, ``"timeout"``.
            duration_sec: Wall-clock duration in seconds.

        Raises:
            ValueError: If *outcome* is not a valid value or
                ``duration_sec`` is negative.
        """
        # tool_name is bounded against the running ToolRegistry's
        # snapshot; fabricated names are rejected at push time so
        # cardinality cannot grow beyond the registry's size.
        validate_tool_name(tool_name)
        require_label("tool outcome", outcome, VALID_TOOL_OUTCOMES)
        require_non_negative("record_tool_invocation: duration_sec", duration_sec)
        self._tool_invocations.labels(
            tool_name=tool_name,
            outcome=outcome,
        ).inc()
        self._tool_duration.labels(
            tool_name=tool_name,
            outcome=outcome,
        ).observe(duration_sec)
