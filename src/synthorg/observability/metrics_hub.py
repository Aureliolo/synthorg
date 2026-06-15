"""Process-global accessor for the active :class:`PrometheusCollector`.

Startup wiring stashes the ``AppState``-owned collector here so call
sites far from ``AppState`` (the cost-recording helper in
:mod:`synthorg.engine.cost_recording`, the tool invocation bridge,
the task engine) can emit provider / task / tool metrics without
needing an async-safe reference back through the dependency graph.

The collector is held behind a weak reference so tests that tear
down ``AppState`` between cases do not keep a dead instance live and
do not accidentally record metrics against the previous run.

``record_*`` wrappers are **best-effort** -- a collector exception
is swallowed and logged so a transient label-validation failure or
internal prometheus_client error cannot take down the business
operation emitting the metric. They also no-op when no collector is
registered so call sites remain safe when metrics are disabled.
"""

import weakref
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.metrics import (
    METRICS_COLLECTOR_ACTIVATED,
    METRICS_COLLECTOR_DEACTIVATED,
    METRICS_RECORD_FAILED,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.observability.prometheus_collector import PrometheusCollector

_P = ParamSpec("_P")
_R = TypeVar("_R")

logger = get_logger(__name__)

_collector_ref: weakref.ReferenceType[PrometheusCollector] | None = None


def set_active_collector(collector: PrometheusCollector) -> None:
    """Register *collector* as the process-active Prometheus collector.

    Idempotent when called with the same instance; overwriting with
    a different instance is expected between tests.
    """
    global _collector_ref  # noqa: PLW0603
    previous = _active()
    _collector_ref = weakref.ref(collector)
    logger.info(
        METRICS_COLLECTOR_ACTIVATED,
        collector=repr(collector),
        previous_collector=repr(previous) if previous is not None else None,
    )


def clear_active_collector() -> None:
    """Drop the process-active collector reference."""
    global _collector_ref  # noqa: PLW0603
    previous = _active()
    _collector_ref = None
    logger.info(
        METRICS_COLLECTOR_DEACTIVATED,
        previous_collector=repr(previous) if previous is not None else None,
    )


def _active() -> PrometheusCollector | None:
    """Return the live collector behind the module weakref, if any.

    Returns:
        The registered ``PrometheusCollector``, or ``None`` when none is
        wired or the weakref target has been collected.
    """
    # Capture the module global once: a concurrent
    # ``clear_active_collector()`` between an ``is None`` check and a
    # second read would otherwise turn the slot to ``None`` and raise
    # ``TypeError: 'NoneType' object is not callable``, which
    # ``_safe_record`` deliberately re-raises into the business path.
    ref = _collector_ref
    if ref is None:
        return None
    return ref()


def _safe_record(
    event: str,
    method: str,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R | None]]:
    """Decorator that swallows and logs collector exceptions.

    Uses :data:`ParamSpec` so the decorated call signatures are
    preserved under strict mypy -- each wrapper keeps its original
    keyword-only arguments visible to callers and checkers.

    Returns:
        A decorator that wraps the target function to catch and log
        non-``TypeError`` collector exceptions, returning ``None`` on
        such a failure.
    """

    def _wrap(fn: Callable[_P, _R]) -> Callable[_P, _R | None]:

        def inner(*args: _P.args, **kwargs: _P.kwargs) -> _R | None:
            try:
                return fn(*args, **kwargs)
            except TypeError:
                # TypeError from a ``record_*`` call almost always
                # means the caller passed wrong-shaped arguments,
                # not a runtime metrics failure. Swallowing that
                # would mask a programming bug; let it propagate
                # so the caller sees the wiring mistake.
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                # ValueError lives under this branch on purpose: it
                # surfaces both genuine programming bugs (caller
                # passed an unknown label) AND transient validation
                # misses caused by the ~15s lag in the registry
                # snapshot (a brand-new agent / workflow / department
                # whose first metric arrives before the next
                # ``refresh()`` seeds the snapshot). Crashing the
                # business path here would punish callers for
                # operator-introduced delays they cannot avoid;
                # tests catch programming bugs by calling
                # ``validate_*`` / ``record_*`` directly, where
                # ValueError still propagates.
                logger.warning(
                    event,
                    hub_method=method,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return None

        return inner

    return _wrap


@_safe_record(METRICS_RECORD_FAILED, "record_provider_usage")
def record_provider_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost: float,
) -> None:
    """Forward to :meth:`PrometheusCollector.record_provider_usage`.

    No-op when no collector is registered so call sites can emit
    metrics without a guard.
    """
    collector = _active()
    if collector is None:
        return
    collector.record_provider_usage(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
    )


@_safe_record(METRICS_RECORD_FAILED, "record_task_run")
def record_task_run(*, outcome: str, duration_sec: float | None) -> None:
    """Forward to :meth:`PrometheusCollector.record_task_run`.

    ``duration_sec=None`` skips the duration-histogram observation
    while still incrementing the outcome counter; see
    :meth:`PrometheusCollector.record_task_run` for the rationale.
    """
    collector = _active()
    if collector is None:
        return
    collector.record_task_run(outcome=outcome, duration_sec=duration_sec)


@_safe_record(METRICS_RECORD_FAILED, "record_security_verdict")
def record_security_verdict(verdict: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_security_verdict`."""
    collector = _active()
    if collector is None:
        return
    collector.record_security_verdict(verdict)


@_safe_record(METRICS_RECORD_FAILED, "record_security_audit_fill_ratio")
def record_security_audit_fill_ratio(*, ratio: float) -> None:
    """Forward to :meth:`PrometheusCollector.record_security_audit_fill_ratio`.

    Bounded ``ratio`` in ``[0.0, 1.0]``; values near ``1.0`` mean the
    next ``AuditLog.record`` evicts the oldest entry.
    """
    collector = _active()
    if collector is None:
        return
    collector.record_security_audit_fill_ratio(ratio=ratio)


@_safe_record(METRICS_RECORD_FAILED, "record_provider_error")
def record_provider_error(
    *,
    provider: str,
    model: str,
    error_class: str,
) -> None:
    """Forward to :meth:`PrometheusCollector.record_provider_error`."""
    collector = _active()
    if collector is None:
        return
    collector.record_provider_error(
        provider=provider,
        model=model,
        error_class=error_class,
    )


@_safe_record(METRICS_RECORD_FAILED, "record_cache_operation")
def record_cache_operation(*, cache_name: str, outcome: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_cache_operation`."""
    collector = _active()
    if collector is None:
        return
    collector.record_cache_operation(cache_name=cache_name, outcome=outcome)


@_safe_record(METRICS_RECORD_FAILED, "record_api_error")
def record_api_error(*, category: str, status_code: int) -> None:
    """Forward to :meth:`PrometheusCollector.record_api_error`."""
    collector = _active()
    if collector is None:
        return
    collector.record_api_error(category=category, status_code=status_code)


@_safe_record(METRICS_RECORD_FAILED, "record_workflow_execution")
def record_workflow_execution(
    *,
    workflow_definition_id: str,
    status: str,
    duration_seconds: float,
) -> None:
    """Forward to :meth:`PrometheusCollector.record_workflow_execution`."""
    collector = _active()
    if collector is None:
        return
    collector.record_workflow_execution(
        workflow_definition_id=workflow_definition_id,
        status=status,
        duration_seconds=duration_seconds,
    )


@_safe_record(METRICS_RECORD_FAILED, "record_tool_invocation")
def record_tool_invocation(
    *,
    tool_name: str,
    outcome: str,
    duration_sec: float,
) -> None:
    """Forward to :meth:`PrometheusCollector.record_tool_invocation`."""
    collector = _active()
    if collector is None:
        return
    collector.record_tool_invocation(
        tool_name=tool_name,
        outcome=outcome,
        duration_sec=duration_sec,
    )


@_safe_record(METRICS_RECORD_FAILED, "record_client_disconnect")
def record_client_disconnect(*, transport: str, reason: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_client_disconnect`."""
    collector = _active()
    if collector is None:
        return
    collector.record_client_disconnect(transport=transport, reason=reason)


@_safe_record(METRICS_RECORD_FAILED, "record_approval_decision")
def record_approval_decision(*, outcome: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_approval_decision`."""
    collector = _active()
    if collector is None:
        return
    collector.record_approval_decision(outcome=outcome)


@_safe_record(METRICS_RECORD_FAILED, "record_escalation_outcome")
def record_escalation_outcome(*, outcome: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_escalation_outcome`."""
    collector = _active()
    if collector is None:
        return
    collector.record_escalation_outcome(outcome=outcome)


@_safe_record(METRICS_RECORD_FAILED, "record_push_queue_event")
def record_push_queue_event(*, outcome: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_push_queue_event`.

    No-op when no collector is registered so the workspace push queue
    can emit metrics without a guard.
    """
    collector = _active()
    if collector is None:
        return
    collector.record_push_queue_event(outcome=outcome)


@_safe_record(METRICS_RECORD_FAILED, "record_blueprint_instantiation")
def record_blueprint_instantiation(
    *,
    outcome: str,
    blueprint_name: str | None = None,
    duration_sec: float | None = None,
) -> None:
    """Forward to :meth:`PrometheusCollector.record_blueprint_instantiation`."""
    collector = _active()
    if collector is None:
        return
    collector.record_blueprint_instantiation(
        outcome=outcome,
        blueprint_name=blueprint_name,
        duration_sec=duration_sec,
    )


@_safe_record(METRICS_RECORD_FAILED, "record_settings_mutation")
def record_settings_mutation(*, namespace: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_settings_mutation`."""
    collector = _active()
    if collector is None:
        return
    collector.record_settings_mutation(namespace=namespace)


@_safe_record(METRICS_RECORD_FAILED, "record_mcp_handler_outcome")
def record_mcp_handler_outcome(
    *,
    tool: str,
    outcome: str,
    duration_sec: float,
) -> None:
    """Forward to :meth:`PrometheusCollector.record_mcp_handler_outcome`."""
    collector = _active()
    if collector is None:
        return
    collector.record_mcp_handler_outcome(
        tool=tool,
        outcome=outcome,
        duration_sec=duration_sec,
    )


@_safe_record(METRICS_RECORD_FAILED, "record_budget_query")
def record_budget_query(*, query_type: str, duration_sec: float) -> None:
    """Forward to :meth:`PrometheusCollector.record_budget_query`."""
    collector = _active()
    if collector is None:
        return
    collector.record_budget_query(
        query_type=query_type,
        duration_sec=duration_sec,
    )


@_safe_record(METRICS_RECORD_FAILED, "record_audit_chain_verification")
def record_audit_chain_verification(
    *,
    outcome: str,
    entries_checked: int,
    first_break_position: int | None = None,
) -> None:
    """Forward to :meth:`PrometheusCollector.record_audit_chain_verification`."""
    collector = _active()
    if collector is None:
        return
    collector.record_audit_chain_verification(
        outcome=outcome,
        entries_checked=entries_checked,
        first_break_position=first_break_position,
    )
