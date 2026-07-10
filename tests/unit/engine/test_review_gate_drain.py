"""Tests for the review-gate shutdown-drain shielding helper.

Covers the non-cancelled paths deterministically. The shutdown-drain branch
(outer ``CancelledError`` mid-await, then a bounded drain) depends on
cancellation timing against a live event loop and is exercised end-to-end by
the review-gate service tests rather than reproduced here as a timing race.
"""

import asyncio

import pytest

from synthorg.engine._review_gate_drain import await_shielded_drain

pytestmark = pytest.mark.unit


async def test_awaits_work_to_completion() -> None:
    completed = False

    async def _work() -> None:
        nonlocal completed
        completed = True

    await await_shielded_drain(
        asyncio.create_task(_work()),
        task_id="task-1",
        approval_id="approval-1",
        decided_by="admin",
    )
    assert completed is True


async def test_work_failure_propagates_when_not_cancelled() -> None:
    # With no outer cancellation, the shielded await surfaces work's own
    # exception directly; the drain branch is only entered on cancellation.
    async def _work() -> None:
        msg = "boom"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        await await_shielded_drain(
            asyncio.create_task(_work()),
            task_id="task-1",
            approval_id=None,
            decided_by="admin",
        )
