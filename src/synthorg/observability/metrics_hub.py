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

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.metrics import (
    METRICS_COLLECTOR_ACTIVATED,
    METRICS_COLLECTOR_DEACTIVATED,
    METRICS_SCRAPE_FAILED,
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
    if _collector_ref is None:
        return None
    return _collector_ref()


def _safe_record(
    event: str,
    method: str,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R | None]]:
    """Decorator that swallows and logs collector exceptions.

    Uses :data:`ParamSpec` so the decorated call signatures are
    preserved under strict mypy -- each wrapper keeps its original
    keyword-only arguments visible to callers and checkers.
    """

    def _wrap(fn: Callable[_P, _R]) -> Callable[_P, _R | None]:
        def inner(*args: _P.args, **kwargs: _P.kwargs) -> _R | None:
            try:
                return fn(*args, **kwargs)
            except MemoryError, RecursionError:
                raise
            except TypeError:
                # TypeError from a ``record_*`` call almost always
                # means the caller passed wrong-shaped arguments,
                # not a runtime metrics failure. Swallowing that
                # would mask a programming bug; let it propagate
                # so the caller sees the wiring mistake.
                raise
            except Exception as exc:
                logger.warning(
                    event,
                    hub_method=method,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return None

        return inner

    return _wrap


@_safe_record(METRICS_SCRAPE_FAILED, "record_provider_usage")
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


@_safe_record(METRICS_SCRAPE_FAILED, "record_task_run")
def record_task_run(*, outcome: str, duration_sec: float) -> None:
    """Forward to :meth:`PrometheusCollector.record_task_run`."""
    collector = _active()
    if collector is None:
        return
    collector.record_task_run(outcome=outcome, duration_sec=duration_sec)


@_safe_record(METRICS_SCRAPE_FAILED, "record_security_verdict")
def record_security_verdict(verdict: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_security_verdict`."""
    collector = _active()
    if collector is None:
        return
    collector.record_security_verdict(verdict)


@_safe_record(METRICS_SCRAPE_FAILED, "record_provider_error")
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


@_safe_record(METRICS_SCRAPE_FAILED, "record_cache_operation")
def record_cache_operation(*, cache_name: str, outcome: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_cache_operation`."""
    collector = _active()
    if collector is None:
        return
    collector.record_cache_operation(cache_name=cache_name, outcome=outcome)


@_safe_record(METRICS_SCRAPE_FAILED, "record_api_error")
def record_api_error(*, category: str, status_code: int) -> None:
    """Forward to :meth:`PrometheusCollector.record_api_error`."""
    collector = _active()
    if collector is None:
        return
    collector.record_api_error(category=category, status_code=status_code)


@_safe_record(METRICS_SCRAPE_FAILED, "record_workflow_execution")
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


@_safe_record(METRICS_SCRAPE_FAILED, "record_tool_invocation")
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


@_safe_record(METRICS_SCRAPE_FAILED, "record_client_disconnect")
def record_client_disconnect(*, transport: str, reason: str) -> None:
    """Forward to :meth:`PrometheusCollector.record_client_disconnect`."""
    collector = _active()
    if collector is None:
        return
    collector.record_client_disconnect(transport=transport, reason=reason)
