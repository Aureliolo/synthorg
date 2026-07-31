# module-kind: code
"""Backup coverage for the readiness surface.

Split from ``health.py`` for the reason memory is: the verdict an operator
needs is a state plus what to do about it, not a boolean, and the controller
module holds only the response models, the fan-out and the routes.

A deployment with no backup coverage serves every request correctly, so the
fault stays invisible until someone needs a recovery point and finds none was
ever taken. Reporting only that backups are absent is barely better: the cause
is decided once at boot, inside a handler nothing else can see, and without it
an operator is left to guess between a missing binary, an unreadable path and a
backend they never configured.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.state import AppState
from synthorg.backup.state import BackupStateSlice


class BackupState(StrEnum):
    """Whether this boot has backup coverage.

    Attributes:
        WIRED: A backup service was built, so recovery points are taken on
            the configured schedule.
        ABSENT: Construction was attempted and failed. No recovery point is
            being taken and every ``backup.*`` setting is inert for the
            lifetime of the process.
        UNATTEMPTED: Backups were never attempted for this boot, which is
            not a verdict about them at all.
    """

    WIRED = "wired"
    ABSENT = "absent"
    UNATTEMPTED = "unattempted"


class BackupHealth(BaseModel):
    """Backup-coverage state.

    Deliberately outside the readiness roll-up, whatever the state: a process
    with no backup coverage still serves traffic correctly, and failing
    readiness over it would have a supervisor restart a healthy deployment
    into the same condition.

    Attributes:
        state: Whether this boot has backup coverage.
        detail: What an operator should do about it, when anything.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    state: BackupState = Field(description="Whether this boot has backup coverage")
    detail: str | None = Field(
        default=None,
        description="Operator-facing remedy, when action is needed",
    )


_ABSENT_SUMMARY = (
    "No backup service could be built, so this deployment is taking no "
    "recovery points and every backup setting is inert until it restarts."
)


def resolve_backup_health(app_state: AppState) -> BackupHealth:
    """Report whether this boot has backup coverage, and why not.

    Reads the wiring rather than probing: the question is whether
    construction succeeded at boot, which the slice already records, so
    there is nothing live to call.

    Nothing is logged here. The boot path already records the failure once
    at ERROR, and this resolver answers every poll of ``/health``, so
    logging would repeat a standing condition on a timer and bury the events
    that describe a change.

    Returns:
        ``BackupHealth`` describing the coverage and, when there is none,
        the cause recorded at boot.
    """
    slice_ = app_state.slice(BackupStateSlice)
    if slice_.service is not None:
        return BackupHealth(state=BackupState.WIRED)
    if not slice_.expected:
        return BackupHealth(state=BackupState.UNATTEMPTED)
    reason = slice_.unavailable_reason
    return BackupHealth(
        state=BackupState.ABSENT,
        detail=_ABSENT_SUMMARY if reason is None else f"{_ABSENT_SUMMARY} {reason}",
    )


__all__ = ["BackupHealth", "BackupState", "resolve_backup_health"]
