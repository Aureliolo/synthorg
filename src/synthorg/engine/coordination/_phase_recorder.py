# module-kind: code
"""One way a coordination phase is timed, logged and recorded.

Every phase wrapper in the service repeated the same shape: start a timer,
log the start, and on failure log it and append a failed
:class:`CoordinationPhaseResult` before raising. Copied per phase, that is
one place per phase for the partial-phase list to be forgotten, and that
list is the only thing a caller reads to see how far the pipeline got
before it died.

Deliberately three small functions rather than one wrapper taking a
callable: the phases are a mix of sync and async work, and a single wrapper
would either force an async signature on the synchronous ones or need a
second copy of itself, which is the duplication this module removes. The
raise itself stays at the call site for the same reason it always has: a
handler that ends in a call rather than a ``raise`` reads as a swallow.
"""

from synthorg.core.clock import Clock
from synthorg.engine.coordination.models import CoordinationPhaseResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_PHASE_FAILED,
    COORDINATION_PHASE_STARTED,
)

logger = get_logger(__name__)


def begin_phase(phase: str, *, clock: Clock) -> float:
    """Announce a phase and return its start reading.

    Args:
        phase: The phase name, as the result list records it.
        clock: Time seam supplying the monotonic reading.

    Returns:
        The monotonic start reading, to be handed back to
        :func:`record_phase_success` or :func:`record_phase_failure`.
    """
    logger.info(COORDINATION_PHASE_STARTED, phase=phase)
    return clock.monotonic()


def record_phase_success(
    phase: str,
    start: float,
    phases: list[CoordinationPhaseResult],
    *,
    clock: Clock,
) -> float:
    """Record a phase that completed.

    Args:
        phase: The phase name.
        start: The reading :func:`begin_phase` returned.
        phases: The running phase list, appended to.
        clock: Time seam supplying the end reading.

    Returns:
        How long the phase took, so a caller can log its own completion line
        with whatever phase-specific counts it has.
    """
    elapsed = clock.monotonic() - start
    phases.append(
        CoordinationPhaseResult(
            phase=phase,
            success=True,
            duration_seconds=elapsed,
        )
    )
    return elapsed


def record_phase_failure(
    phase: str,
    start: float,
    phases: list[CoordinationPhaseResult],
    *,
    clock: Clock,
    exc: Exception,
    summary: str,
) -> str:
    """Record a phase that failed and return the message for its error.

    Records and describes; it does not raise. The ``raise
    CoordinationPhaseError`` stays at the call site, which is what the
    fail-loud gate reads and what tells the next reader that the handler
    does not swallow.

    Args:
        phase: The phase name.
        start: The reading :func:`begin_phase` returned.
        phases: The running phase list, appended to here so the failed entry
            is already in it when the caller snapshots it.
        clock: Time seam supplying the end reading.
        exc: What went wrong, redacted into the message.
        summary: Human-readable prefix, e.g. ``"Routing failed"``.

    Returns:
        The redacted failure message.
    """
    description = safe_error_description(exc)
    logger.warning(
        COORDINATION_PHASE_FAILED,
        phase=phase,
        error_type=type(exc).__name__,
        error=description,
    )
    phases.append(
        CoordinationPhaseResult(
            phase=phase,
            success=False,
            duration_seconds=clock.monotonic() - start,
            error=description,
        )
    )
    return f"{summary}: {description}"


__all__ = ["begin_phase", "record_phase_failure", "record_phase_success"]
