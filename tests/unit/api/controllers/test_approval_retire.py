"""Tests for retiring the approvals about a row before the row is deleted.

A pending approval outlives the plan or task it names. Deciding it afterwards
drives the resume path at an id that resolves to nothing, so the delete has to
take the approval with it, and a delete that is refused has to leave the queue
as it found it.
"""

import asyncio
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest

from synthorg.api.controllers._approval_retire import (
    RetiredApprovals,
    retiring_approvals_for_tasks,
    retiring_plan_approvals,
    retiring_task_approvals,
)
from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, mock_of, sid
from tests._shared.app_state import make_app_state

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
_PLAN_ID = str(as_uuid("doomed"))
_TASK_ID = sid("task-doomed")


def _approval(
    approval_id: str,
    *,
    source: ApprovalSource = ApprovalSource.PLAN_REVIEW,
    plan_id: str | None = _PLAN_ID,
    task_id: str | None = None,
) -> ApprovalItem:
    metadata = {} if plan_id is None else {PLAN_ID_METADATA_KEY: plan_id}
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=NotBlankStr("plan:approve"),
        title=NotBlankStr("Approve plan"),
        description=NotBlankStr("1 subtask(s)"),
        requested_by=NotBlankStr("user-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=source,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        task_id=None if task_id is None else NotBlankStr(task_id),
        metadata=metadata,
    )


class _RecordingStore:
    """Approval store double recording the conditional writes it received."""

    def __init__(self, items: tuple[ApprovalItem, ...]) -> None:
        self._items = items
        self.saved: list[ApprovalItem] = []
        self.restored: list[ApprovalItem] = []
        # ``False`` stands for a write landing between the read and the
        # conditional write, which is what the real store reports by
        # answering ``None``. It reports the same for a row that has gone or
        # already expired, so ``refused_status`` says which this stands for.
        self.cas_wins = True
        self.refused_status: ApprovalStatus | None = ApprovalStatus.APPROVED

    async def list_items(self, **_: object) -> tuple[ApprovalItem, ...]:
        return self._items

    async def get(self, approval_id: UUID | str) -> ApprovalItem | None:
        if self.refused_status is None:
            return None
        found = next(
            (i for i in self._items if str(i.id) == str(approval_id)),
            None,
        )
        if found is None:
            return None
        return found.model_copy(update={"status": self.refused_status})

    async def save(self, item: ApprovalItem) -> ApprovalItem | None:
        self.restored.append(item)
        return item

    async def save_if_pending(self, item: ApprovalItem) -> ApprovalItem | None:
        self.saved.append(item)
        return item if self.cas_wins else None


def _state(store: _RecordingStore | None) -> object:
    """Build an app state carrying *store* on the approval slice.

    Returns:
        The composed ``AppState``.
    """
    return make_app_state(
        slices={ApprovalStateSlice: {"store": store}} if store else None
    )


async def _run(
    retirement: contextlib.AbstractAsyncContextManager[RetiredApprovals],
    *removed: str,
) -> None:
    """Retire, with a delete that succeeds.

    Args:
        retirement: The scope returned by an entry point.
        removed: The subject ids the delete removed. Defaults to both ids
            under test, which is what a delete that succeeds reports.
    """
    async with retirement as handle:
        handle.removed(*(removed or (_PLAN_ID, _TASK_ID)))


async def _run_refusing(
    retirement: contextlib.AbstractAsyncContextManager[RetiredApprovals],
    refusal: BaseException,
) -> None:
    """Retire, with a delete that refuses.

    Args:
        retirement: The scope returned by an entry point. ``BaseException``
            because cancellation is one of the ways a delete does not happen.
        refusal: What the delete raises inside it.
    """
    async with retirement:
        raise refusal


async def _run_partial(
    retirement: contextlib.AbstractAsyncContextManager[RetiredApprovals],
    removed: tuple[str, ...],
    refusal: BaseException,
) -> None:
    """Retire, remove *removed*, then fail on what is left.

    Args:
        retirement: The scope returned by an entry point.
        removed: The subject ids the delete got through before failing.
        refusal: What the next delete raises.
    """
    async with retirement as handle:
        handle.removed(*removed)
        raise refusal


async def _run_declining(
    retirement: contextlib.AbstractAsyncContextManager[RetiredApprovals],
) -> None:
    """Retire, with a delete that declines without raising.

    Args:
        retirement: The scope returned by an entry point.
    """
    async with retirement:
        pass


#: Each retire entry point bound to the id it is asked about, so the shared
#: behaviour (store failure, concurrent decision, unwired store) is asserted
#: once per entry point rather than once for whichever was written first.
_Retire = Callable[[object], contextlib.AbstractAsyncContextManager[RetiredApprovals]]

_ENTRY_POINTS: tuple[tuple[str, _Retire], ...] = (
    ("plan", lambda state: retiring_plan_approvals(state, _PLAN_ID)),  # type: ignore[arg-type]  # composed AppState
    ("task", lambda state: retiring_task_approvals(state, _TASK_ID)),  # type: ignore[arg-type]  # composed AppState
)


class TestRetirePlanApprovals:
    async def test_the_plans_own_pending_approval_is_expired(self) -> None:
        store = _RecordingStore((_approval("parked"),))

        await _run(retiring_plan_approvals(_state(store), _PLAN_ID))  # type: ignore[arg-type]  # composed AppState

        assert [item.status for item in store.saved] == [ApprovalStatus.EXPIRED]
        assert store.saved[0].id == as_uuid("parked")

    async def test_another_plans_approval_is_left_alone(self) -> None:
        """The metadata is the link; matching on source alone would take it."""
        store = _RecordingStore(
            (_approval("other", plan_id=str(as_uuid("other-plan"))),)
        )

        await _run(retiring_plan_approvals(_state(store), _PLAN_ID))  # type: ignore[arg-type]  # composed AppState

        assert store.saved == []

    async def test_a_non_review_approval_is_left_alone(self) -> None:
        store = _RecordingStore((_approval("gate", source=ApprovalSource.REVIEW_GATE),))

        await _run(retiring_plan_approvals(_state(store), _PLAN_ID))  # type: ignore[arg-type]  # composed AppState

        assert store.saved == []


class TestRetireTaskApprovals:
    async def test_the_tasks_pending_review_is_expired(self) -> None:
        """The four rows a live teardown left decidable against nothing."""
        store = _RecordingStore(
            (
                _approval(
                    "failed-review",
                    source=ApprovalSource.REVIEW_GATE,
                    plan_id=None,
                    task_id=_TASK_ID,
                ),
            )
        )

        await _run(retiring_task_approvals(_state(store), _TASK_ID))  # type: ignore[arg-type]  # composed AppState

        assert [item.status for item in store.saved] == [ApprovalStatus.EXPIRED]

    @pytest.mark.parametrize("source", list(ApprovalSource))
    async def test_every_source_goes_with_the_task(
        self, source: ApprovalSource
    ) -> None:
        """Which sources asked about a task is a property of the run.

        Cases come from the enum, so a source added later is covered without
        anyone remembering to widen a hand-written list.
        """
        store = _RecordingStore(
            (_approval("asked", source=source, plan_id=None, task_id=_TASK_ID),)
        )

        await _run(retiring_task_approvals(_state(store), _TASK_ID))  # type: ignore[arg-type]  # composed AppState

        assert [item.status for item in store.saved] == [ApprovalStatus.EXPIRED]

    async def test_another_tasks_approval_is_left_alone(self) -> None:
        store = _RecordingStore(
            (
                _approval(
                    "elsewhere",
                    source=ApprovalSource.REVIEW_GATE,
                    plan_id=None,
                    task_id=sid("task-other"),
                ),
            )
        )

        await _run(retiring_task_approvals(_state(store), _TASK_ID))  # type: ignore[arg-type]  # composed AppState

        assert store.saved == []

    async def test_an_approval_naming_no_task_is_left_alone(self) -> None:
        store = _RecordingStore((_approval("planwide"),))

        await _run(retiring_task_approvals(_state(store), _TASK_ID))  # type: ignore[arg-type]  # composed AppState

        assert store.saved == []


@pytest.mark.parametrize(
    ("label", "retire"), _ENTRY_POINTS, ids=[name for name, _ in _ENTRY_POINTS]
)
class TestRetirementGatesTheDelete:
    """Shared across entry points: retiring is a precondition, not a courtesy."""

    async def test_a_store_failure_stops_the_delete(
        self, label: str, retire: _Retire
    ) -> None:
        """Retiring gates the delete, so a store that cannot answer blocks it.

        Swallowing here is what the ordering was inverted to remove: the row
        would be deleted next while its approval stayed decidable, and the
        only remaining move would be to log a window that is already open.
        """
        del label
        store = mock_of[ApprovalStoreProtocol]()
        store.list_items.side_effect = RuntimeError("store down")

        with pytest.raises(RuntimeError, match="store down"):
            await _run(retire(_state(store)))

    async def test_a_concurrent_decision_refuses_the_delete(
        self, label: str, retire: _Retire
    ) -> None:
        """A verdict made while the row existed outranks the deletion."""
        store = _RecordingStore(
            (
                _approval(
                    "parked",
                    source=ApprovalSource.PLAN_REVIEW,
                    task_id=_TASK_ID,
                ),
            )
        )
        store.cas_wins = False

        with pytest.raises(ConflictError, match=f"{label} .*no longer pending"):
            await _run(retire(_state(store)))

    @pytest.mark.parametrize(
        "already",
        [ApprovalStatus.EXPIRED, None],
        ids=["already_expired", "already_gone"],
    )
    async def test_a_concurrent_delete_does_not_refuse_this_one(
        self,
        label: str,
        retire: _Retire,
        already: ApprovalStatus | None,
    ) -> None:
        """Two deletes of the same row: the second is satisfied, not blocked."""
        del label
        store = _RecordingStore(
            (
                _approval(
                    "parked",
                    source=ApprovalSource.PLAN_REVIEW,
                    task_id=_TASK_ID,
                ),
            )
        )
        store.cas_wins = False
        store.refused_status = already

        await _run(retire(_state(store)))

    async def test_an_unwired_store_is_a_no_op(
        self, label: str, retire: _Retire
    ) -> None:
        del label
        await _run(retire(_state(None)))


class TestRefusalLeavesTheQueueAlone:
    """A refused delete must not leave half a task's questions dead."""

    async def test_the_approvals_already_expired_are_put_back(self) -> None:
        first = _approval(
            "first",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        second = _approval(
            "second",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        store = _RecordingStore((first, second))

        # The first write lands, the second is refused by a real decision.
        original = store.save_if_pending

        async def _first_only(item: ApprovalItem) -> ApprovalItem | None:
            store.cas_wins = item.id == first.id
            return await original(item)

        store.save_if_pending = _first_only  # type: ignore[method-assign]

        with pytest.raises(ConflictError):
            await _run(retiring_task_approvals(_state(store), _TASK_ID))  # type: ignore[arg-type]  # composed AppState

        assert [item.id for item in store.restored] == [first.id]
        assert store.restored[0].status is ApprovalStatus.PENDING

    async def test_a_store_that_breaks_partway_puts_back_what_it_expired(
        self,
    ) -> None:
        """The store failing on the second write owes back the first.

        Nothing has been deleted at this point, so every approval expired so
        far decides about a row that is still there. The loop is inside the
        recovery scope for exactly this: a failure before the body starts is
        as much a delete that did not happen as one raised inside it.
        """
        first = _approval(
            "first",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        second = _approval(
            "second",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        store = _RecordingStore((first, second))
        original = store.save_if_pending

        async def _then_break(item: ApprovalItem) -> ApprovalItem | None:
            if item.id == second.id:
                msg = "store down"
                raise RuntimeError(msg)
            return await original(item)

        store.save_if_pending = _then_break  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="store down"):
            await _run(retiring_task_approvals(_state(store), _TASK_ID))  # type: ignore[arg-type]  # composed AppState

        assert [item.id for item in store.restored] == [first.id]
        assert store.restored[0].status is ApprovalStatus.PENDING

    async def test_a_delete_that_fails_puts_every_approval_back(self) -> None:
        """The refusal an operator can provoke on purpose.

        ``delete_task`` refuses a task a plan still names as its objective. If
        retirement were merely sequenced before it, anyone with write access
        could strip a task's pending reviews by issuing a delete they know
        will be refused, and retrying the delete would not bring them back.
        """
        asked = _approval(
            "asked",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        store = _RecordingStore((asked,))
        refusal = ConflictError("a plan still names this task as its objective")

        with pytest.raises(ConflictError, match="objective"):
            await _run_refusing(
                retiring_task_approvals(_state(store), _TASK_ID),  # type: ignore[arg-type]  # composed AppState
                refusal,
            )

        assert [item.status for item in store.saved] == [ApprovalStatus.EXPIRED]
        assert [item.id for item in store.restored] == [asked.id]
        assert store.restored[0].status is ApprovalStatus.PENDING

    async def test_a_delete_that_succeeds_restores_nothing(self) -> None:
        asked = _approval(
            "asked",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        store = _RecordingStore((asked,))

        await _run(retiring_task_approvals(_state(store), _TASK_ID))  # type: ignore[arg-type]  # composed AppState

        assert store.restored == []

    async def test_a_delete_that_declines_without_raising_puts_them_back(
        self,
    ) -> None:
        """A teardown skips a plan whose live tasks refuse it, and returns.

        Nothing raises on that path, so a scope that only undid on an exception
        would leave the review approval expired against a plan still listed,
        still reviewable, and now unanswerable.
        """
        parked = _approval("parked")
        store = _RecordingStore((parked,))

        await _run_declining(retiring_plan_approvals(_state(store), _PLAN_ID))  # type: ignore[arg-type]  # composed AppState

        assert [item.status for item in store.saved] == [ApprovalStatus.EXPIRED]
        assert [item.id for item in store.restored] == [parked.id]
        assert store.restored[0].status is ApprovalStatus.PENDING

    async def test_a_cancelled_delete_puts_them_back(self) -> None:
        """Cancellation is not an ``Exception``, and it is not a delete either.

        A request abandoned mid-delete leaves the row exactly where it was, so
        an approval expired on its behalf has to go back with it.
        """
        asked = _approval(
            "asked",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        store = _RecordingStore((asked,))

        with pytest.raises(asyncio.CancelledError):
            await _run_refusing(
                retiring_task_approvals(_state(store), _TASK_ID),  # type: ignore[arg-type]  # composed AppState
                asyncio.CancelledError(),
            )

        assert [item.id for item in store.restored] == [asked.id]
        assert store.restored[0].status is ApprovalStatus.PENDING


class TestRetiringManyTasksAtOnce:
    async def test_one_pass_covers_every_named_task(self) -> None:
        """The cascade reads the queue once, not once per child."""
        mine = _approval(
            "mine",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        theirs = _approval(
            "theirs",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=sid("task-other"),
        )
        elsewhere = _approval(
            "elsewhere",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=sid("task-unrelated"),
        )
        store = _RecordingStore((mine, theirs, elsewhere))

        await _run(
            retiring_approvals_for_tasks(
                _state(store),  # type: ignore[arg-type]  # composed AppState
                [_TASK_ID, sid("task-other")],
            ),
            _TASK_ID,
            sid("task-other"),
        )

        assert {item.id for item in store.saved} == {mine.id, theirs.id}
        assert store.restored == []

    async def test_a_page_that_fails_partway_keeps_the_removed_ones_expired(
        self,
    ) -> None:
        """Restoring the whole page would resurrect four dead questions.

        The cascade deletes a page of tasks one at a time under one retirement.
        A failure on the fifth leaves the first four gone, and their approvals
        decide about nothing; only the survivors are owed their questions back.
        """
        gone = _approval(
            "gone",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=_TASK_ID,
        )
        survivor = _approval(
            "survivor",
            source=ApprovalSource.REVIEW_GATE,
            plan_id=None,
            task_id=sid("task-other"),
        )
        store = _RecordingStore((gone, survivor))

        refusal = ConflictError("the fifth delete refused")

        with pytest.raises(ConflictError, match="fifth"):
            await _run_partial(
                retiring_approvals_for_tasks(
                    _state(store),  # type: ignore[arg-type]  # composed AppState
                    [_TASK_ID, sid("task-other")],
                ),
                (_TASK_ID,),
                refusal,
            )

        assert [item.id for item in store.restored] == [survivor.id]
        assert store.restored[0].status is ApprovalStatus.PENDING

    async def test_an_empty_set_touches_nothing(self) -> None:
        store = _RecordingStore(())

        await _run(retiring_approvals_for_tasks(_state(store), []))  # type: ignore[arg-type]  # composed AppState

        assert store.saved == []
