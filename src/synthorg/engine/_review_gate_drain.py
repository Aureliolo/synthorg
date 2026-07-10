# module-kind: code
"""Shutdown-drain shielding for the review gate's background audit writes.

The decision-record + deliverable-receipt side effects of a review decision
run as a possibly-backgrounded task. A graceful shutdown cancels that task, so
the write must be drained to completion (bounded) before the cancellation is
allowed to propagate, or a COMPLETED task could be left without its decision
record. Extracted from ``review_gate`` so that module stays within its size
tier; it is a self-contained concern shared by the transition and
acknowledgement decision paths.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_AUDIT_DRAIN_FAILED,
)

logger = get_logger(__name__)

# Upper bound on draining a shielded audit write during a shutdown
# cancellation: an internal safety cap (never an operator knob), so a hung
# write cannot block shutdown indefinitely. Past it, the write keeps running
# detached and the cancellation propagates.
_SHIELD_DRAIN_TIMEOUT_SECONDS: Final[float] = 5.0


async def await_shielded_drain(
    work: asyncio.Task[None],
    *,
    task_id: str,
    approval_id: str | None,
    decided_by: str,
) -> None:
    """Await a task to completion even under shutdown-drain cancellation.

    A bare ``await asyncio.shield(work)`` is not enough: cancelling the outer
    task re-raises ``CancelledError`` here while ``work`` runs on detached, so
    the loop can close before it lands. On cancellation, drain ``work``
    (bounded, so a hung write cannot block shutdown forever) before
    propagating. The drain is wrapped so that ``work``'s own failure or a
    drain timeout can never mask the cancellation that must propagate.

    The record/receipt seams ``work`` runs log only their own expected faults,
    so any exception that surfaces here (a drain ``TimeoutError``, or an
    unexpected error from the write itself) has no other record; this logs
    ``APPROVAL_GATE_REVIEW_AUDIT_DRAIN_FAILED`` (keyed by ``task_id`` /
    ``approval_id`` so it is attributable) before re-raising the cancellation.

    Raises:
        CancelledError: Re-raised after ``work`` has drained (or the drain
            bound elapsed), so a shutdown-drain cancellation propagates
            without losing the shielded side effects.
    """
    try:
        await asyncio.shield(work)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(
                asyncio.shield(work),
                timeout=_SHIELD_DRAIN_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the drained work's failure / a drain
            # timeout must not replace the cancellation the outer ``raise``
            # below re-propagates.
            reraise_critical(exc)
            # A TimeoutError means the bound elapsed with the write still in
            # flight; any other exception is the write failing on its own.
            # Neither was logged by ``work``, so surface it accurately.
            note = (
                "shielded audit write did not drain within the bound "
                "under shutdown cancellation"
                if isinstance(exc, TimeoutError)
                else "shielded audit write raised while draining under "
                "shutdown cancellation"
            )
            logger.warning(
                APPROVAL_GATE_REVIEW_AUDIT_DRAIN_FAILED,
                task_id=task_id,
                approval_id=approval_id,
                decided_by=decided_by,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note=note,
            )
        raise
