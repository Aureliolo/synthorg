"""Unit tests for ``wire_review_staffing`` / ``unwire_review_staffing``.

The sweep is what makes a staffing park temporary, so this wiring is
load-bearing in a way a constructed-but-not-started service would hide: the
scheduler must actually be running, the roster must actually reach it, and a
teardown that fails to stop it must still stop reporting it as up.
"""

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import Final
from unittest.mock import AsyncMock

import pytest

from synthorg.api.lifecycle_helpers.review_staffing_wiring import (
    unwire_review_staffing,
    wire_review_staffing,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.core.task import Task
from synthorg.core.task_enums import STAFFING_BLOCKED_REASONS
from synthorg.core.types import NotBlankStr
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.routing_policy import CapabilityPolicy
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.state import HrStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.persistence.task_protocol import TaskRepository
from tests._shared import as_uuid, make_app_state, mock_of
from tests._shared.staffing import roster_capability_policy

pytestmark = pytest.mark.unit

#: One backlog read per staffing park, so a whole pass is observable by count.
_READS_PER_PASS: Final[int] = len(STAFFING_BLOCKED_REASONS)

#: Generous ceiling on waiting for a sweep that should arrive in microseconds;
#: it bounds a hang, it is not a cadence.
_WAIT_SECONDS: Final[float] = 5.0


class _SweepProbe:
    """Counts backlog reads so a sweep can be awaited rather than slept on."""

    def __init__(self, *, target: int) -> None:
        self.reads = 0
        self._target = target
        self.reached = asyncio.Event()

    def expect(self, target: int) -> None:
        """Arm the probe for the *target*-th read."""
        self._target = target
        self.reached.clear()
        if self.reads >= target:
            self.reached.set()

    async def query(self, *_args: object, **_kwargs: object) -> tuple[Task, ...]:
        """Record a backlog read and report an empty page.

        Returns:
            No parked tasks, so the pass finishes without transitioning any.
        """
        self.reads += 1
        if self.reads >= self._target:
            self.reached.set()
        return ()

    async def swept(self) -> None:
        """Wait for the armed read, failing the test rather than hanging."""
        async with asyncio.timeout(_WAIT_SECONDS):
            await self.reached.wait()


def _app_state(
    *,
    backend: object | None = object(),
    with_task_engine: bool = True,
    with_registry: bool = True,
    with_review_gate: bool = True,
) -> AppState:
    """App state carrying everything the sweep declares it needs.

    Returns:
        The composed ``AppState``.
    """
    return make_app_state(
        slices={
            EngineStateSlice: {
                "task_engine": mock_of[TaskEngine]() if with_task_engine else None
            },
            HrStateSlice: {
                "agent_registry": AgentRegistryService() if with_registry else None
            },
            ApprovalStateSlice: {
                "review_gate": (
                    mock_of[ReviewGateService]() if with_review_gate else None
                )
            },
            PersistenceStateSlice: {"backend": backend},
        },
    )


def _patch_persistence(monkeypatch: pytest.MonkeyPatch, probe: _SweepProbe) -> None:
    """Point the wiring's persistence read at *probe*."""
    tasks = mock_of[TaskRepository](query=AsyncMock(side_effect=probe.query))
    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.review_staffing_wiring.persistence_of",
        lambda _state: SimpleNamespace(tasks=tasks),
    )


def _patch_capability(
    monkeypatch: pytest.MonkeyPatch,
    policy: CapabilityPolicy | None = None,
) -> None:
    """Stand in for the process-wide capability policy the sweep selects with.

    Building the real one needs a provider catalogue, which this wiring test
    is not about; ``None`` is the no-provider boot the sweep declines on.
    """

    async def _build(_state: object) -> CapabilityPolicy | None:
        return policy

    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.review_staffing_wiring.build_capability_policy",
        _build,
    )


def _wired(monkeypatch: pytest.MonkeyPatch, probe: _SweepProbe) -> None:
    """Patch both seams the wiring reaches outside its own slice."""
    _patch_persistence(monkeypatch, probe)
    _patch_capability(monkeypatch, roster_capability_policy())


def _holder(role: str = COMPLETION_REVIEWER_ROLE_NAME) -> AgentIdentity:
    """Build a roster agent whose arrival may reach the sweep.

    Returns:
        An identity holding *role*.
    """
    return AgentIdentity(
        id=as_uuid(f"late-{role}"),
        name=NotBlankStr("Ada"),
        role=NotBlankStr(role),
        department=NotBlankStr("Quality Assurance"),
        model=ModelConfig(
            provider=NotBlankStr("example-provider"),
            model_id=NotBlankStr("example-capable-001"),
            capability="capable",
        ),
        hiring_date=date(2026, 1, 15),
    )


async def test_the_wired_sweep_is_actually_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A constructed-but-unstarted scheduler would report up and sweep nothing."""
    probe = _SweepProbe(target=_READS_PER_PASS)
    _wired(monkeypatch, probe)
    app_state = _app_state()

    await wire_review_staffing(app_state)
    try:
        await probe.swept()
        scheduler = app_state.slice(EngineStateSlice).review_staffing_scheduler
        assert scheduler is not None
        assert scheduler.is_running
    finally:
        await unwire_review_staffing(app_state)


async def test_a_roster_change_cuts_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cadence is the guarantee; the listener is what makes it prompt.

    With a fifteen-minute cadence, a second pass inside this test can only
    come from the roster nudge, so this fails if the listener is never
    installed.
    """
    probe = _SweepProbe(target=_READS_PER_PASS)
    _wired(monkeypatch, probe)
    app_state = _app_state()

    await wire_review_staffing(app_state)
    try:
        await probe.swept()
        probe.expect(_READS_PER_PASS * 2)

        registry = app_state.slice(HrStateSlice).agent_registry
        assert registry is not None
        await registry.register(_holder())

        await probe.swept()
    finally:
        await unwire_review_staffing(app_state)


async def test_an_ordinary_hire_does_not_wake_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep releases work parked for want of a judge, and nothing else.

    Waking on every roster mutation turned an org loading its whole staff
    into one full backlog walk per agent, none of which could release a
    thing.
    """
    probe = _SweepProbe(target=_READS_PER_PASS)
    _wired(monkeypatch, probe)
    app_state = _app_state()

    await wire_review_staffing(app_state)
    try:
        await probe.swept()
        reads_after_boot = probe.reads

        registry = app_state.slice(HrStateSlice).agent_registry
        assert registry is not None
        await registry.register(_holder("Backend Developer"))
        for _ in range(_READS_PER_PASS * 2):
            await asyncio.sleep(0)

        assert probe.reads == reads_after_boot
    finally:
        await unwire_review_staffing(app_state)


async def test_already_wired_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _SweepProbe(target=_READS_PER_PASS)
    _wired(monkeypatch, probe)
    app_state = _app_state()

    await wire_review_staffing(app_state)
    try:
        first = app_state.slice(EngineStateSlice).review_staffing_scheduler
        await wire_review_staffing(app_state)
        assert app_state.slice(EngineStateSlice).review_staffing_scheduler is first
    finally:
        await unwire_review_staffing(app_state)


@pytest.mark.parametrize(
    ("kwargs", "condition"),
    [
        ({"backend": None}, "no persistence backend"),
        ({"with_task_engine": False}, "no task engine"),
        ({"with_registry": False}, "no agent registry"),
        ({"with_review_gate": False}, "no review gate"),
    ],
)
async def test_declines_naming_the_absent_collaborator(
    kwargs: dict[str, object], condition: str
) -> None:
    """`GET /subsystems` answers "why is this not up", so it must be named."""
    app_state = _app_state(**kwargs)  # type: ignore[arg-type]  # parametrised flags

    with pytest.raises(SubsystemDeclinedError, match=condition):
        await wire_review_staffing(app_state)

    assert app_state.slice(EngineStateSlice).review_staffing_scheduler is None


async def test_declines_when_no_capability_policy_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no provider configured nothing grades a model, so the sweep has
    no bar to staff a gate role against."""
    _patch_persistence(monkeypatch, _SweepProbe(target=_READS_PER_PASS))
    _patch_capability(monkeypatch, None)
    app_state = _app_state()

    with pytest.raises(SubsystemDeclinedError, match="no capability policy"):
        await wire_review_staffing(app_state)

    assert app_state.slice(EngineStateSlice).review_staffing_scheduler is None


async def test_unwiring_stops_the_sweep_and_drops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _SweepProbe(target=_READS_PER_PASS)
    _wired(monkeypatch, probe)
    app_state = _app_state()
    await wire_review_staffing(app_state)
    await probe.swept()
    scheduler = app_state.slice(EngineStateSlice).review_staffing_scheduler
    assert scheduler is not None

    await unwire_review_staffing(app_state)

    assert app_state.slice(EngineStateSlice).review_staffing_scheduler is None
    assert not scheduler.is_running


async def test_a_roster_change_after_unwiring_reaches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener left pointing at a stopped sweep fires into nothing."""
    probe = _SweepProbe(target=_READS_PER_PASS)
    _wired(monkeypatch, probe)
    app_state = _app_state()
    await wire_review_staffing(app_state)
    await probe.swept()
    await unwire_review_staffing(app_state)
    reads_at_teardown = probe.reads

    registry = app_state.slice(HrStateSlice).agent_registry
    assert registry is not None
    await registry.register(_holder())
    # Yield generously: a surviving listener would have to run somewhere.
    for _ in range(_READS_PER_PASS * 2):
        await asyncio.sleep(0)

    assert probe.reads == reads_at_teardown


async def test_a_failed_stop_still_drops_the_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving it published would report the sweep up with nothing running."""
    probe = _SweepProbe(target=_READS_PER_PASS)
    _wired(monkeypatch, probe)
    app_state = _app_state()
    await wire_review_staffing(app_state)
    await probe.swept()
    scheduler = app_state.slice(EngineStateSlice).review_staffing_scheduler
    assert scheduler is not None
    real_stop = scheduler.stop

    async def _stop_that_hangs() -> None:
        """Stand in for a drain that blew its deadline."""
        msg = "drain exceeded the hard deadline"
        raise TimeoutError(msg)

    monkeypatch.setattr(scheduler, "stop", _stop_that_hangs)

    await unwire_review_staffing(app_state)

    assert app_state.slice(EngineStateSlice).review_staffing_scheduler is None
    await real_stop()
