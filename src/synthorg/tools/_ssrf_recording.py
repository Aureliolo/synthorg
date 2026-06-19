# module-kind: feature
"""Fail-safe recording seam for SSRF pre-flight rejections.

The shared SSRF chokepoint (:func:`synthorg.tools.ssrf.resolve_outbound_target`)
is a low-level pure guard with no access to persistence. To turn the
previously write-never ``SsrfViolation`` store into a live audit trail
without coupling the guard to the persistence layer, boot installs a
recorder via :func:`install_ssrf_violation_recorder`; the guard then
calls :func:`record_ssrf_violation` on every rejection.

Recording is strictly best-effort and FAIL-SAFE: any non-critical error
while recording is swallowed (``MemoryError`` / ``RecursionError`` are
re-raised via :func:`reraise_critical`) so a recording failure can never
turn an SSRF block into a crash or let a blocked request through. When no
recorder is installed (recording subsystem off, or unit-test scope) the
call is a no-op.
"""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_SSRF_VIOLATION_RECORD_FAILED,
)

logger = get_logger(__name__)

#: Recorder callable: ``(url, hostname, port, resolved_ip, blocked_range)``.
#: The boot-installed implementation stamps a timestamp from the wired
#: clock, redacts the URL, and writes a PENDING ``SsrfViolation`` row.
SsrfViolationRecorder = Callable[
    [str, str, int, str | None, str | None], Awaitable[None]
]

_recorder: ContextVar[SsrfViolationRecorder | None] = ContextVar(
    "ssrf_violation_recorder", default=None
)


def install_ssrf_violation_recorder(recorder: SsrfViolationRecorder | None) -> None:
    """Publish (or clear) the active SSRF-violation recorder.

    Called once at boot when the recording subsystem is wired; pass
    ``None`` to disable (the chokepoint then no-ops).
    """
    _recorder.set(recorder)


async def record_ssrf_violation(
    *,
    url: str,
    hostname: str,
    port: int,
    resolved_ip: str | None = None,
    blocked_range: str | None = None,
) -> None:
    """Best-effort, fail-safe record of one SSRF rejection.

    A no-op when no recorder is installed. Any recording error is
    swallowed (criticals re-raised) so the SSRF block it instruments can
    never be weakened by a persistence failure.
    """
    recorder = _recorder.get()
    if recorder is None:
        return
    try:
        await recorder(url, hostname, port, resolved_ip, blocked_range)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # Distinct event from the block itself so a SIEM reader never
        # confuses a recording failure with an actual outbound block.
        logger.warning(
            SECURITY_SSRF_VIOLATION_RECORD_FAILED,
            hostname=hostname,
            port=port,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
