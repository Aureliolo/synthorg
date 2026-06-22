"""Distributed-worker error hierarchy.

Rooted in :class:`DomainError` so the API layer's centralised RFC 9457
dispatch picks up every subtype, mirroring
:mod:`synthorg.core.persistence_errors`.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class WorkerError(DomainError):
    """Base exception for distributed-worker subsystem failures."""

    default_message: ClassVar[str] = "Distributed worker operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class WorkerDeadLetterError(WorkerError):
    """Raised when a dead-letter claim cannot be driven to ``FAILED``.

    The dead-letter consumer's whole purpose is to guarantee no task is
    silently lost when JetStream exhausts ``max_deliver``. If it cannot
    transition the task (the transition seam permanently rejects, or
    the dedup write fails after the transition), that guarantee is
    broken; surface a loud typed error rather than ack-and-forget.
    """

    default_message: ClassVar[str] = "Dead-letter claim could not be failed"
    error_code: ClassVar[ErrorCode] = ErrorCode.WORKER_DEAD_LETTER_ERROR


class WorkerUnrestartableError(WorkerError):
    """Base for background-worker services unrestartable after a timed-out stop.

    The canonical lifecycle pattern (docs/reference/lifecycle-sync.md): a
    stop whose drain exceeds the hard deadline leaves the original loop
    alive on the instance and marks the service unrestartable, so a later
    ``start`` raises rather than stacking a second loop on the orphan.
    Subclasses keep the inherited ``INTERNAL_ERROR`` code (an inheritance
    alias) since the error is raised at boot wiring, never returned over
    HTTP.
    """

    default_message: ClassVar[str] = (
        "Worker service is unrestartable after a timed-out stop"
    )


class WorkerHeartbeatUnrestartableError(WorkerUnrestartableError):
    """Raised when the heartbeat subscriber is restarted after a timed-out stop."""

    default_message: ClassVar[str] = (
        "Worker heartbeat subscriber is unrestartable after a timed-out stop"
    )


class SeenClaimsPrunerUnrestartableError(WorkerUnrestartableError):
    """Raised when the seen-claims pruner is restarted after a timed-out stop."""

    default_message: ClassVar[str] = (
        "Seen-claims pruner is unrestartable after a timed-out stop"
    )


class DeadLetterConsumerUnrestartableError(WorkerUnrestartableError):
    """Raised when the dead-letter consumer is restarted after a timed-out stop."""

    default_message: ClassVar[str] = (
        "Dead-letter consumer is unrestartable after a timed-out stop"
    )
