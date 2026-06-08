"""Unit tests for the training session methods.

Exercises the in-memory session store that :meth:`TrainingService.start_session`
populates. The pipeline itself is mocked via a subclass override of
``execute`` so these tests stay focused on the session bookkeeping
(ordering, pagination, FIFO eviction, terminal state recording).
"""

from datetime import UTC, datetime

import pytest
import structlog.testing

from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.training.models import (
    ContentType,
    TrainingPlan,
    TrainingPlanStatus,
    TrainingResult,
)
from synthorg.hr.training.service import TrainingService
from synthorg.observability.events.hr import (
    HR_TRAINING_SESSION_RECORD_FAILED as _RECORD_FAILED_EVENT,
)
from synthorg.observability.events.hr import (
    HR_TRAINING_SESSION_RECORDED as _RECORDED_EVENT,
)
from synthorg.observability.events.training import (
    HR_TRAINING_PLAN_STATUS_TRANSITIONED as _TRANSITION_EVENT,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)


def _plan(plan_id: str = "plan-1", new_agent_id: str = "agent-new") -> TrainingPlan:
    return TrainingPlan(
        id=NotBlankStr(plan_id),
        new_agent_id=NotBlankStr(new_agent_id),
        new_agent_role=NotBlankStr("engineer"),
        new_agent_level=SeniorityLevel.MID,
        enabled_content_types=frozenset({ContentType.PROCEDURAL}),
        created_at=_NOW,
    )


def _result(plan_id: str = "plan-1", new_agent_id: str = "agent-new") -> TrainingResult:
    return TrainingResult(
        plan_id=NotBlankStr(plan_id),
        new_agent_id=NotBlankStr(new_agent_id),
        started_at=_NOW,
        completed_at=_NOW,
    )


class _NoopSelector:
    """Minimal selector protocol stub -- never invoked in session tests."""

    async def select_sources(self, plan: TrainingPlan) -> tuple[object, ...]:
        return ()


class _NoopCuration:
    """Minimal curation protocol stub -- never invoked in session tests."""

    async def curate(  # type: ignore[no-untyped-def]
        self, items, plan, content_type
    ):
        return ()


class _NoopMemoryBackend:
    """Minimal memory backend stub -- never invoked in session tests."""

    async def store(self, request):  # type: ignore[no-untyped-def]
        return None


def _build_service(*, raises: Exception | None = None) -> TrainingService:
    """Construct a real :class:`TrainingService` wired to noop stubs.

    The session-method tests exercise only ``start_session`` /
    ``list_sessions`` / ``get_session``, which delegate pipeline work
    to ``_execute_locked``. Constructing the full service (rather than
    a subclass that bypasses ``__init__``) ensures that when
    ``TrainingService.__init__`` gains new required state, these tests
    fail with a clear ``TypeError`` instead of silently missing the
    new attribute.

    The returned service has ``_execute_locked`` monkey-patched onto
    the instance so ``start_session`` sees a predictable synthetic
    result (or the requested exception).
    """
    service = TrainingService(
        selector=_NoopSelector(),  # type: ignore[arg-type]
        extractors={},
        curation=_NoopCuration(),  # type: ignore[arg-type]
        guards=(),
        memory_backend=_NoopMemoryBackend(),  # type: ignore[arg-type]
    )
    calls: list[str] = []

    async def _fake_execute_locked(
        plan: TrainingPlan,
    ) -> tuple[TrainingResult, bool]:
        calls.append(str(plan.id))
        if raises is not None:
            raise raises
        return _result(str(plan.id), str(plan.new_agent_id)), True

    # Bind to the instance so future ``__init__`` additions don't
    # affect this test surface; the real class method remains
    # available via ``type(service)._execute_locked`` if needed.
    service._execute_locked = _fake_execute_locked  # type: ignore[method-assign]
    service.calls = calls  # type: ignore[attr-defined]
    return service


class TestStartSession:
    """Happy path + failure path."""

    async def test_records_executed_status_on_success(self) -> None:
        service = _build_service()
        plan = _plan("plan-1")

        result = await service.start_session(plan)

        assert result.plan_id == "plan-1"
        session = await service.get_session(NotBlankStr("plan-1"))
        assert session is not None
        assert session.status == TrainingPlanStatus.EXECUTED
        assert session.executed_at is not None

    async def test_records_failed_status_on_exception(self) -> None:
        boom = RuntimeError("pipeline exploded")
        service = _build_service(raises=boom)
        plan = _plan("plan-2")

        with pytest.raises(RuntimeError, match="pipeline exploded"):
            await service.start_session(plan)

        session = await service.get_session(NotBlankStr("plan-2"))
        assert session is not None
        assert session.status == TrainingPlanStatus.FAILED
        assert session.executed_at is not None


class TestListSessions:
    """Ordering + pagination + FIFO eviction."""

    async def test_newest_first(self) -> None:
        service = _build_service()
        for idx in range(3):
            await service.start_session(
                _plan(plan_id=f"plan-{idx}", new_agent_id=f"agent-{idx}"),
            )

        page, total = await service.list_sessions(offset=0, limit=50)

        assert total == 3
        assert [s.id for s in page] == ["plan-2", "plan-1", "plan-0"]

    async def test_paginates(self) -> None:
        service = _build_service()
        for idx in range(5):
            await service.start_session(
                _plan(plan_id=f"plan-{idx}", new_agent_id=f"agent-{idx}"),
            )

        page, total = await service.list_sessions(offset=2, limit=2)

        assert total == 5
        assert [s.id for s in page] == ["plan-2", "plan-1"]

    async def test_empty_returns_zero_total(self) -> None:
        service = _build_service()

        page, total = await service.list_sessions(offset=0, limit=50)

        assert total == 0
        assert page == ()

    async def test_negative_offset_rejects(self) -> None:
        service = _build_service()

        with pytest.raises(ValueError, match="offset"):
            await service.list_sessions(offset=-1, limit=50)

    async def test_non_positive_limit_rejects(self) -> None:
        service = _build_service()

        with pytest.raises(ValueError, match="limit"):
            await service.list_sessions(offset=0, limit=0)


class TestGetSession:
    """Present + missing."""

    async def test_returns_plan_when_present(self) -> None:
        service = _build_service()
        await service.start_session(_plan("plan-1"))

        session = await service.get_session(NotBlankStr("plan-1"))

        assert session is not None
        assert session.id == "plan-1"

    async def test_returns_none_when_missing(self) -> None:
        service = _build_service()

        session = await service.get_session(NotBlankStr("nope"))

        assert session is None


class TestSessionCap:
    """FIFO eviction beyond the in-memory cap."""

    async def test_oldest_sessions_evicted_beyond_cap(self) -> None:
        from synthorg.hr.training import service as svc_mod

        # Shrink the cap to keep the test fast while still exercising
        # the eviction loop.
        original = svc_mod._SESSION_STORE_MAX
        svc_mod._SESSION_STORE_MAX = 3
        try:
            service = _build_service()
            for idx in range(5):
                await service.start_session(
                    _plan(
                        plan_id=f"plan-{idx}",
                        new_agent_id=f"agent-{idx}",
                    ),
                )

            page, total = await service.list_sessions(offset=0, limit=10)
        finally:
            svc_mod._SESSION_STORE_MAX = original

        # Older entries dropped; the 3 most recent survive.
        assert total == 3
        assert [s.id for s in page] == ["plan-4", "plan-3", "plan-2"]


class TestStatusTransitionLogs:
    """Status-transition INFO log on every persisted PENDING -> EXECUTED /
    PENDING -> FAILED hop in :meth:`TrainingService.start_session`."""

    async def test_executed_transition_emits_status_transitioned_event(
        self,
    ) -> None:
        service = _build_service()
        plan = _plan("plan-1")

        with structlog.testing.capture_logs() as events:
            await service.start_session(plan)

        transitions = [e for e in events if e.get("event") == _TRANSITION_EVENT]
        assert len(transitions) == 1
        entry = transitions[0]
        assert entry["plan_id"] == "plan-1"
        assert entry["from_status"] == TrainingPlanStatus.PENDING.value
        assert entry["to_status"] == TrainingPlanStatus.EXECUTED.value

    async def test_failed_transition_emits_status_transitioned_event(
        self,
    ) -> None:
        boom = RuntimeError("pipeline exploded")
        service = _build_service(raises=boom)
        plan = _plan("plan-2")

        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(RuntimeError, match="pipeline exploded"),
        ):
            await service.start_session(plan)

        transitions = [e for e in events if e.get("event") == _TRANSITION_EVENT]
        assert len(transitions) == 1
        entry = transitions[0]
        assert entry["plan_id"] == "plan-2"
        assert entry["from_status"] == TrainingPlanStatus.PENDING.value
        assert entry["to_status"] == TrainingPlanStatus.FAILED.value

    async def test_status_transitioned_emitted_after_record_session(
        self,
    ) -> None:
        """The transition log fires AFTER the persistence write succeeds.

        Pinned by CLAUDE.md "every persisted hop logs at INFO using a
        domain-scoped *_STATUS_TRANSITIONED constant ... AFTER the
        persistence write succeeds".
        """
        service = _build_service()
        plan = _plan("plan-3")

        with structlog.testing.capture_logs() as events:
            await service.start_session(plan)

        ordering: list[str] = []
        for event in events:
            name = event.get("event")
            if name == _RECORDED_EVENT:
                if event.get("status") == TrainingPlanStatus.EXECUTED.value:
                    ordering.append("recorded_executed")
            elif name == _TRANSITION_EVENT:
                ordering.append("transitioned")

        assert ordering == ["recorded_executed", "transitioned"], (
            f"transition log must come AFTER the executed session record; "
            f"observed order: {ordering}"
        )

    async def test_status_transitioned_skipped_when_record_fails(self) -> None:
        """No EXECUTED transition log when the executed-branch persistence write raised.

        The session-store write is the persistence gate; a failure there
        means the state change is not durable and the audit stream must
        not falsely record the hop. Tests the EXECUTED branch (line 326
        of `start_session`): entry write succeeds, pipeline runs, then
        the terminal-record write blows up.
        """
        service = _build_service()
        plan = _plan("plan-4")

        # Stub the entry write to succeed once; raise on the terminal
        # (executed-branch) write. Without this counter, replacing
        # `_record_session` with an unconditionally-raising stub would
        # also blow up the entry write (line 288 of start_session) and
        # the test would not actually exercise the executed-branch
        # contract its docstring claims to pin.
        call_count = 0
        original_record = service._record_session

        async def _exploding_on_terminal(plan: TrainingPlan) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Entry write: succeed via the real implementation.
                await original_record(plan)
                return
            msg = "session store offline"
            raise RuntimeError(msg)

        service._record_session = _exploding_on_terminal  # type: ignore[method-assign]

        with structlog.testing.capture_logs() as events:
            await service.start_session(plan)

        transitions = [e for e in events if e.get("event") == _TRANSITION_EVENT]
        record_failed = [e for e in events if e.get("event") == _RECORD_FAILED_EVENT]

        assert transitions == [], (
            "transition log must NOT fire when the executed-branch persistence "
            "write fails"
        )
        executed_failures = [e for e in record_failed if e.get("stage") == "executed"]
        assert executed_failures, (
            "the executed-stage persistence-failure WARNING must still fire so "
            "operators see the durable-write failure"
        )

    async def test_failed_branch_record_failure_skips_transition(self) -> None:
        """No FAILED transition log when the failed-branch persistence write raised.

        Mirror of the executed-branch test but for the FAILED side
        (line 307 of `start_session`): the pipeline raises, the
        failed-record write also raises, and the transition log MUST
        NOT fire.
        """
        boom = RuntimeError("pipeline exploded")
        service = _build_service(raises=boom)
        plan = _plan("plan-4b")

        call_count = 0
        original_record = service._record_session

        async def _exploding_on_terminal(plan: TrainingPlan) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await original_record(plan)
                return
            msg = "session store offline"
            raise RuntimeError(msg)

        service._record_session = _exploding_on_terminal  # type: ignore[method-assign]

        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(RuntimeError, match="pipeline exploded"),
        ):
            await service.start_session(plan)

        transitions = [e for e in events if e.get("event") == _TRANSITION_EVENT]
        record_failed = [e for e in events if e.get("event") == _RECORD_FAILED_EVENT]

        assert transitions == [], (
            "transition log must NOT fire when the failed-branch persistence "
            "write fails"
        )
        failed_stage = [e for e in record_failed if e.get("stage") == "failed"]
        assert failed_stage, (
            "the failed-stage persistence-failure WARNING must still fire"
        )

    async def test_idempotent_reentry_no_duplicate_transition_log(self) -> None:
        """A second `start_session` on a terminal plan emits no transition log.

        Re-entry on a plan whose terminal session was already recorded
        must short-circuit (`ran_pipeline=False`) without emitting
        another `*_STATUS_TRANSITIONED` event. Without this contract,
        an idempotent retry loop would amplify the audit stream with
        phantom transitions.

        The default ``_build_service`` fake makes ``_execute_locked``
        always report ``ran_pipeline=True``; that masks the
        idempotency short-circuit we need to pin here, so this test
        installs a counter-aware fake that returns ``ran_pipeline=False``
        on the second call to mirror the real ``_execute_locked``
        behavior when the plan id is already in
        ``self._executed_plan_ids``.
        """
        service = _build_service()
        plan = _plan("plan-5")

        invocations = 0

        async def _idempotent_execute_locked(
            plan_arg: TrainingPlan,
        ) -> tuple[TrainingResult, bool]:
            nonlocal invocations
            invocations += 1
            ran_pipeline = invocations == 1
            return _result(str(plan_arg.id), str(plan_arg.new_agent_id)), ran_pipeline

        service._execute_locked = _idempotent_execute_locked  # type: ignore[assignment, method-assign]

        await service.start_session(plan)

        with structlog.testing.capture_logs() as events:
            result = await service.start_session(plan)

        transitions = [e for e in events if e.get("event") == _TRANSITION_EVENT]
        assert transitions == [], (
            "second start_session on an already-executed plan must NOT emit a "
            "transition log; the EXECUTED branch is gated on ran_pipeline=True"
        )
        assert result is not None
        assert invocations == 2
