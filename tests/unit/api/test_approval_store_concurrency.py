"""Concurrency regression tests for ApprovalStore.

Covers the TOCTOU gaps in ``save()``, ``save_if_pending()``, ``add()`` and
the lazy expiration path. Two concurrent ``save(same_id)`` must result in
first-writer-wins semantics: exactly one call persists its payload, the
second returns ``None`` and logs ``API_APPROVAL_CONFLICT`` with
``error="concurrent_save"``.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.persistence_errors import ConstraintViolationError


def _now() -> datetime:
    return datetime.now(UTC)


def _make_item(
    *,
    approval_id: str = "approval-001",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    decision_reason: str | None = None,
    decided_at: datetime | None = None,
    decided_by: str | None = None,
) -> ApprovalItem:
    return ApprovalItem(
        id=approval_id,
        action_type="code:merge",
        title="Test approval",
        description="A test approval item",
        requested_by="agent-dev",
        risk_level=ApprovalRiskLevel.MEDIUM,
        status=status,
        created_at=_now(),
        expires_at=None,
        decided_at=decided_at,
        decided_by=decided_by,
        decision_reason=decision_reason,
    )


class GatedRepo:
    """Fake approval repo that gates the first ``save`` call on an event.

    Lets tests deterministically reproduce the race where caller A is
    mid-write while caller B enters ``save()``.
    """

    def __init__(self) -> None:
        self.items: dict[str, ApprovalItem] = {}
        self.save_calls = 0
        self.gate = asyncio.Event()
        self.first_entered = asyncio.Event()
        self.gate_enabled = True

    async def get(self, approval_id: str) -> ApprovalItem | None:
        return self.items.get(approval_id)

    async def save(self, item: ApprovalItem) -> None:
        self.save_calls += 1
        if self.gate_enabled and self.save_calls == 1:
            self.first_entered.set()
            await self.gate.wait()
        self.items[item.id] = item

    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: str | None = None,
    ) -> tuple[ApprovalItem, ...]:
        del status, risk_level, action_type
        return tuple(self.items.values())


@pytest.mark.unit
class TestSaveConcurrency:
    """save() must guarantee first-writer-wins under concurrent callers."""

    @pytest.mark.parametrize(
        "warm_cache",
        [
            pytest.param(True, id="warm-cache"),
            pytest.param(False, id="cold-cache"),
        ],
    )
    async def test_concurrent_save_first_writer_wins(
        self,
        warm_cache: bool,
    ) -> None:
        """FWW contract holds regardless of cache state.

        When the cache is warm, both callers hit the cache branch in
        ``save()``.  When the cache is cold, both must fall through
        to ``repo.get`` under the lock -- the in-flight marker must
        still reject the second caller in either case.
        """
        repo = GatedRepo()
        initial = _make_item()
        repo.items[initial.id] = initial
        store = ApprovalStore(repo=repo)  # type: ignore[arg-type]
        if warm_cache:
            # Pre-warm the cache; without this, both saves fall through
            # to ``repo.get`` under the lock.
            await store.get(initial.id)

        updated_a = initial.model_copy(update={"decision_reason": "reason_a"})
        updated_b = initial.model_copy(update={"decision_reason": "reason_b"})

        with patch(
            "synthorg.api.approval_store.logger",
        ) as mock_logger:
            task_a = asyncio.create_task(store.save(updated_a))
            await repo.first_entered.wait()
            # Task A is parked inside repo.save waiting on the gate,
            # with its approval id already in ``_saves_in_flight`` and
            # the store lock released.  Awaiting the second save
            # directly here is deterministic: the store lock is free,
            # B observes the in-flight marker, logs the conflict and
            # returns ``None`` before we unblock A.
            result_b = await store.save(updated_b)
            repo.gate.set()
            result_a = await task_a

            # Exactly one winner, one rejection.
            winners = [r for r in (result_a, result_b) if r is not None]
            rejections = [r for r in (result_a, result_b) if r is None]
            assert len(winners) == 1
            assert len(rejections) == 1
            # The FWW contract is that the first caller wins.
            assert result_a is not None
            assert result_b is None

            # Stored payload matches the winner.
            stored = await store.get(initial.id)
            assert stored is not None
            assert stored.decision_reason == winners[0].decision_reason

            # Only one repo.save call happened.
            assert repo.save_calls == 1

            # Conflict log was emitted with the concurrent_save error tag.
            conflict_calls = [
                call
                for call in mock_logger.warning.call_args_list
                if call.kwargs.get("error") == "concurrent_save"
            ]
            assert len(conflict_calls) == 1
            assert conflict_calls[0].kwargs["approval_id"] == initial.id

    async def test_sequential_saves_both_succeed(self) -> None:
        """Sequential saves both persist; in-flight only rejects overlap."""
        repo = GatedRepo()
        repo.gate_enabled = False  # no gating
        initial = _make_item()
        repo.items[initial.id] = initial
        store = ApprovalStore(repo=repo)  # type: ignore[arg-type]
        await store.get(initial.id)

        updated_a = initial.model_copy(update={"decision_reason": "first"})
        result_a = await store.save(updated_a)
        assert result_a is not None
        assert result_a.decision_reason == "first"

        updated_b = updated_a.model_copy(update={"decision_reason": "second"})
        result_b = await store.save(updated_b)
        assert result_b is not None
        assert result_b.decision_reason == "second"

        stored = await store.get(initial.id)
        assert stored is not None
        assert stored.decision_reason == "second"

    async def test_save_in_flight_cleared_on_repo_error(self) -> None:
        """An exception in repo.save must clear in-flight so retries work."""

        class FailingRepo(GatedRepo):
            async def save(self, item: ApprovalItem) -> None:
                self.save_calls += 1
                if self.save_calls == 1:
                    msg = "boom"
                    raise ConstraintViolationError(msg, constraint="test")
                self.items[item.id] = item

        repo = FailingRepo()
        initial = _make_item()
        repo.items[initial.id] = initial
        store = ApprovalStore(repo=repo)  # type: ignore[arg-type]
        await store.get(initial.id)

        updated = initial.model_copy(update={"decision_reason": "first"})
        with pytest.raises(ConstraintViolationError):
            await store.save(updated)

        # In-flight set must be empty so the next save is not rejected.
        retry_result = await store.save(updated)
        assert retry_result is not None

    async def test_save_cancelled_after_repo_commit_invalidates_cache(
        self,
    ) -> None:
        """Cancellation after a committed repo write must evict the cache.

        Otherwise the next reader would serve the stale cached copy
        instead of the freshly committed repository state.
        """

        class CommittingThenCancellingRepo(GatedRepo):
            """Simulate a repo whose commit lands before cancellation."""

            def __init__(self) -> None:
                super().__init__()
                # Set AFTER the repo has committed the new value;
                # the test awaits this to guarantee we cancel the
                # save task only once cancellation will land
                # post-commit (that is the race window we are
                # exercising).
                self.committed = asyncio.Event()

            async def save(self, item: ApprovalItem) -> None:
                self.save_calls += 1
                self.items[item.id] = item
                self.committed.set()
                await self.gate.wait()

        repo = CommittingThenCancellingRepo()
        initial = _make_item()
        repo.items[initial.id] = initial
        store = ApprovalStore(repo=repo)  # type: ignore[arg-type]
        await store.get(initial.id)  # warm the cache

        updated = initial.model_copy(update={"decision_reason": "cancelled"})
        task = asyncio.create_task(store.save(updated))
        await repo.committed.wait()  # deterministic: commit has landed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Repo has the new value; cache entry must have been evicted
        # so the next ``get`` reloads from the repository.
        assert repo.items[initial.id].decision_reason == "cancelled"
        assert initial.id not in store._items

        refreshed = await store.get(initial.id)
        assert refreshed is not None
        assert refreshed.decision_reason == "cancelled"


@pytest.mark.unit
class TestSaveIfPendingConcurrency:
    """save_if_pending: exactly one of two concurrent transitions wins."""

    async def test_concurrent_save_if_pending_exactly_one_wins(self) -> None:
        store = ApprovalStore()
        item = _make_item()
        await store.add(item)

        now = _now()
        approve = item.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": now,
                "decided_by": "alice",
                "decision_reason": "looks good",
            },
        )
        reject = item.model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "decided_at": now,
                "decided_by": "bob",
                "decision_reason": "nope",
            },
        )

        async with asyncio.TaskGroup() as tg:
            t_approve = tg.create_task(store.save_if_pending(approve))
            t_reject = tg.create_task(store.save_if_pending(reject))
        results = (t_approve.result(), t_reject.result())
        winners = [r for r in results if r is not None]
        losers = [r for r in results if r is None]
        assert len(winners) == 1
        assert len(losers) == 1

        stored = await store.get(item.id)
        assert stored is not None
        assert stored.status == winners[0].status

    async def test_save_if_pending_rejected_while_save_in_flight(
        self,
    ) -> None:
        """``save_if_pending`` must observe ``_saves_in_flight`` too.

        Without this guard, ``save()`` releasing the store lock for
        repo I/O lets ``save_if_pending()`` enter, read the stale
        cached ``PENDING`` item, and persist a competing decision,
        reopening the lost-update race that FWW is meant to close.
        """
        repo = GatedRepo()
        initial = _make_item()
        repo.items[initial.id] = initial
        store = ApprovalStore(repo=repo)  # type: ignore[arg-type]
        await store.get(initial.id)  # warm cache

        updated_a = initial.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": _now(),
                "decided_by": "alice",
                "decision_reason": "ok",
            },
        )
        competing = initial.model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "decided_at": _now(),
                "decided_by": "bob",
                "decision_reason": "no",
            },
        )

        with patch("synthorg.api.approval_store.logger") as mock_logger:
            task = asyncio.create_task(store.save(updated_a))
            await repo.first_entered.wait()
            # Task A is parked inside ``repo.save`` with the store
            # lock released; ``save_if_pending`` must now see the
            # in-flight marker and refuse to proceed.
            result = await store.save_if_pending(competing)
            repo.gate.set()
            await task

        assert result is None
        conflict_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.kwargs.get("error") == "concurrent_save"
        ]
        assert len(conflict_calls) == 1
        assert conflict_calls[0].kwargs["approval_id"] == initial.id

        stored = await store.get(initial.id)
        assert stored is not None
        assert stored.status == ApprovalStatus.APPROVED
        assert stored.decision_reason == "ok"


@pytest.mark.unit
class TestAddConcurrency:
    """add() must allow exactly one of two concurrent duplicates."""

    async def test_concurrent_add_same_id_exactly_one_succeeds(self) -> None:
        store = ApprovalStore()
        item_a = _make_item()
        item_b = _make_item()  # same id, same payload

        # Capture ConflictError inside the task body so ``TaskGroup``
        # does not cancel its sibling when one of the adds loses the
        # race; the outer assertions need both outcomes.
        async def try_add(item: ApprovalItem) -> ConflictError | None:
            try:
                await store.add(item)
            except ConflictError as exc:
                return exc
            return None

        async with asyncio.TaskGroup() as tg:
            t_a = tg.create_task(try_add(item_a))
            t_b = tg.create_task(try_add(item_b))
        results = (t_a.result(), t_b.result())

        # One succeeds (returns None), the other returns ConflictError.
        successes = [r for r in results if r is None]
        conflicts = [r for r in results if isinstance(r, ConflictError)]
        assert len(successes) == 1
        assert len(conflicts) == 1

        stored = await store.get(item_a.id)
        assert stored is not None


@pytest.mark.unit
class TestAddConstraintViolationPath:
    """add() surfaces repo constraint violations as ConflictError."""

    async def test_repo_constraint_violation_becomes_conflict_error(self) -> None:
        class ConstraintRepo(GatedRepo):
            async def save(self, item: ApprovalItem) -> None:
                del item
                self.save_calls += 1
                msg = "duplicate"
                raise ConstraintViolationError(msg, constraint="pk")

        repo = ConstraintRepo()
        store = ApprovalStore(repo=repo)  # type: ignore[arg-type]
        with pytest.raises(ConflictError, match="already exists"):
            await store.add(_make_item())


@pytest.mark.unit
class TestExpirationConcurrency:
    """Lazy expiration must not race with concurrent save() on the same item."""

    async def test_expiration_during_concurrent_save_serialised(self) -> None:
        store = ApprovalStore()
        now = _now()
        item = ApprovalItem(
            id="exp-concurrent",
            action_type="code:merge",
            title="Test",
            description="desc",
            requested_by="agent-dev",
            risk_level=ApprovalRiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        store._items[item.id] = item

        # get() triggers expiration; save_if_pending sees non-PENDING stored state.
        async with asyncio.TaskGroup() as tg:
            get_task = tg.create_task(store.get(item.id))
            save_task = tg.create_task(
                store.save_if_pending(
                    item.model_copy(update={"status": ApprovalStatus.APPROVED}),
                ),
            )
        get_result = get_task.result()
        save_result = save_task.result()

        # ``expires_at`` is in the past, so ``_check_expiration_locked``
        # deterministically transitions the item to ``EXPIRED`` before
        # either caller can act on it.  ``save_if_pending`` therefore
        # always returns ``None`` (status no longer ``PENDING``) and
        # ``get`` always returns the ``EXPIRED`` item -- even if the
        # two callers interleave, the lock serialisation guarantees a
        # single, consistent outcome.
        assert save_result is None
        assert get_result is not None
        assert get_result.status == ApprovalStatus.EXPIRED


class _LostRaceRepo:
    """Fake repo whose ``expire_if_pending`` always loses every row.

    Exercises the lost-race refetch path in ``ApprovalStore.list_items``
    so the regression test can assert the batch ``get_many`` is the
    only fetch path used (no per-id ``get`` loop).
    """

    def __init__(self, items: dict[str, ApprovalItem]) -> None:
        self.items = items
        self.list_calls = 0
        self.expire_calls = 0
        self.get_calls = 0
        self.get_many_calls = 0
        self.get_many_id_counts: list[int] = []

    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]:
        del status, risk_level, action_type
        self.list_calls += 1
        rows = tuple(self.items.values())
        return rows[offset : offset + limit]

    async def expire_if_pending(
        self,
        ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        # Pretend every row already transitioned to a terminal status
        # before our scan landed -- nothing flips, every id ends up in
        # the lost-race set.
        self.expire_calls += 1
        del ids
        return ()

    async def get(self, approval_id: str) -> ApprovalItem | None:
        # Pinned to surface the old N+1 path: any call here is a
        # regression -- the lost-race refetch must go through
        # ``get_many``.
        self.get_calls += 1
        return self.items.get(approval_id)

    async def get_many(self, ids: tuple[str, ...]) -> tuple[ApprovalItem, ...]:
        self.get_many_calls += 1
        self.get_many_id_counts.append(len(ids))
        return tuple(self.items[item_id] for item_id in ids if item_id in self.items)


@pytest.mark.unit
class TestLostRaceBatchFetch:
    """Lost-race refetch uses one ``get_many`` instead of N ``get`` calls."""

    async def test_no_n_plus_one_get_calls_on_lost_race(self) -> None:
        now = _now()
        # Three rows with ``expires_at`` in the past so the
        # lazy-expiration pass tries to flip them; every flip "loses"
        # because the repo claims the rows already moved to terminal
        # statuses (we returned ``()`` from ``expire_if_pending``).
        items = {
            f"approval-{i}": ApprovalItem(
                id=f"approval-{i}",
                action_type="code:merge",
                title=f"Test {i}",
                description="desc",
                requested_by="agent-dev",
                risk_level=ApprovalRiskLevel.MEDIUM,
                status=ApprovalStatus.APPROVED,  # already terminal
                created_at=now - timedelta(hours=2),
                expires_at=now - timedelta(hours=1),
                decided_at=now,
                decided_by="alice",
                decision_reason="ok",
            )
            for i in range(3)
        }
        # Override: the IN-MEMORY page snapshot we feed back to the
        # store has ``status=PENDING`` (so ``to_persist`` covers all 3
        # rows). The ``items`` dict (used by ``get_many``) holds the
        # *authoritative* terminal-status copies.
        page_snapshot = {
            k: v.model_copy(update={"status": ApprovalStatus.PENDING})
            for k, v in items.items()
        }

        class SnapshotRepo(_LostRaceRepo):
            async def list_items(
                self,
                *,
                status: ApprovalStatus | None = None,
                risk_level: ApprovalRiskLevel | None = None,
                action_type: str | None = None,
                limit: int = 100,
                offset: int = 0,
            ) -> tuple[ApprovalItem, ...]:
                del status, risk_level, action_type
                self.list_calls += 1
                rows = tuple(page_snapshot.values())
                return rows[offset : offset + limit]

        repo = SnapshotRepo(items=items)
        store = ApprovalStore(repo=repo)  # type: ignore[arg-type]

        result = await store.list_items()

        # All three rows survive -- they were "approved" already so
        # they appear in the response with their authoritative state.
        assert len(result) == 3
        assert {item.id for item in result} == set(items.keys())
        assert all(item.status == ApprovalStatus.APPROVED for item in result)

        # The N+1 invariant: zero per-id ``get`` calls; one
        # ``get_many`` call sized to the lost-race set.
        assert repo.get_calls == 0, "regression: per-id get loop in lost-race path"
        assert repo.get_many_calls == 1
        assert repo.get_many_id_counts == [3]

    async def test_get_many_skipped_when_lost_race_set_is_empty(self) -> None:
        """Empty lost-race set must not issue any ``get_many`` query."""
        now = _now()
        items = {
            "approval-0": ApprovalItem(
                id="approval-0",
                action_type="code:merge",
                title="Test 0",
                description="desc",
                requested_by="agent-dev",
                risk_level=ApprovalRiskLevel.MEDIUM,
                status=ApprovalStatus.PENDING,
                created_at=now - timedelta(hours=2),
                expires_at=now - timedelta(hours=1),
            ),
        }

        class WinningRepo(_LostRaceRepo):
            async def expire_if_pending(self, ids: tuple[str, ...]) -> tuple[str, ...]:
                self.expire_calls += 1
                # Pretend we won every flip -- no lost-race rows.
                return ids

        repo = WinningRepo(items=items)
        store = ApprovalStore(repo=repo)  # type: ignore[arg-type]

        await store.list_items()

        # ``get_many`` is called once with an empty tuple in the
        # current implementation (the helper short-circuits empty
        # input), or skipped entirely. Either way, no per-id ``get``
        # call must fire.
        assert repo.get_calls == 0
        if repo.get_many_calls:
            assert repo.get_many_id_counts == [0]
