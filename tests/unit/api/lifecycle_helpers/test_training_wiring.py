"""Training either comes up, or declines naming the condition that stopped it.

As a one-shot startup hook this ran once, before the durable memory backend was
guaranteed to exist, and any failure inside it was swallowed into a
``severity=non_fatal`` warning. The only thing an operator could see was
``eval_loop`` reporting "no training service" for the life of the process, which
names a symptom belonging to somebody else.

So two things are pinned here. Each absent precondition raises a
:class:`SubsystemDeclinedError` that names ITSELF, and a failure nobody
anticipated propagates rather than being turned into a warning: an activation
that swallows is one that cannot be reported on.
"""

import pytest

from synthorg.api.lifecycle_helpers.training_wiring import (
    unwire_training_service,
    wire_training_service,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.config.schema import RootConfig
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.state import HrStateSlice
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.state import MemoryStateSlice
from synthorg.tools.invocation_tracker import ToolInvocationTracker
from synthorg.tools.state import ToolsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _config(*, enabled: bool = True) -> RootConfig:
    """Build a root config whose training section is on or off.

    Returns:
        The configuration.
    """
    config = RootConfig(company_name="test")
    return config.model_copy(
        update={"training": config.training.model_copy(update={"enabled": enabled})},
    )


def _app_state(
    *,
    tracker: PerformanceTracker | None = None,
    invocation_tracker: ToolInvocationTracker | None = None,
    memory_backend: MemoryBackend | None = None,
) -> AppState:
    """Build an app state whose named collaborators are present, others absent.

    Returns:
        The composed state.
    """
    return make_app_state(
        agent_registry=mock_of[AgentRegistryService](),
        approval_store=mock_of[ApprovalStoreProtocol](),
        performance_tracker=tracker,
        slices={
            ToolsStateSlice: {"invocation_tracker": invocation_tracker},
            MemoryStateSlice: {"backend": memory_backend},
        },
    )


def _complete_app_state() -> AppState:
    """Build an app state with every training precondition satisfied.

    Returns:
        The composed state.
    """
    return _app_state(
        tracker=mock_of[PerformanceTracker](),
        invocation_tracker=mock_of[ToolInvocationTracker](),
        memory_backend=mock_of[MemoryBackend](),
    )


class TestItComesUp:
    async def test_a_complete_boot_installs_the_service(self) -> None:
        app_state = _complete_app_state()

        await wire_training_service(app_state, _config())

        assert app_state.slice(HrStateSlice).training_service is not None

    async def test_a_second_pass_leaves_the_installed_one_alone(self) -> None:
        # The reconciler is level-triggered, so activation runs again on every
        # pass; rebuilding each time would hand out a fresh service to anyone
        # who re-read the slice and leave the old one serving whoever did not.
        app_state = _complete_app_state()
        await wire_training_service(app_state, _config())
        first = app_state.slice(HrStateSlice).training_service

        await wire_training_service(app_state, _config())

        assert app_state.slice(HrStateSlice).training_service is first


class TestItDeclinesNamingItsOwnCondition:
    async def test_no_configuration(self) -> None:
        with pytest.raises(SubsystemDeclinedError, match="configuration"):
            await wire_training_service(_complete_app_state(), None)

    async def test_training_switched_off(self) -> None:
        with pytest.raises(SubsystemDeclinedError, match=r"training\.enabled"):
            await wire_training_service(_complete_app_state(), _config(enabled=False))

    async def test_no_tool_invocation_tracker(self) -> None:
        app_state = _app_state(
            tracker=mock_of[PerformanceTracker](),
            memory_backend=mock_of[MemoryBackend](),
        )

        with pytest.raises(SubsystemDeclinedError, match="tool-invocation tracker"):
            await wire_training_service(app_state, _config())

    async def test_no_performance_tracker(self) -> None:
        app_state = _app_state(
            invocation_tracker=mock_of[ToolInvocationTracker](),
            memory_backend=mock_of[MemoryBackend](),
        )

        with pytest.raises(SubsystemDeclinedError, match="performance tracker"):
            await wire_training_service(app_state, _config())

    async def test_no_memory_backend(self) -> None:
        app_state = _app_state(
            tracker=mock_of[PerformanceTracker](),
            invocation_tracker=mock_of[ToolInvocationTracker](),
        )

        with pytest.raises(SubsystemDeclinedError, match="memory backend"):
            await wire_training_service(app_state, _config())

    async def test_a_decline_installs_nothing(self) -> None:
        app_state = _app_state(tracker=mock_of[PerformanceTracker]())

        with pytest.raises(SubsystemDeclinedError):
            await wire_training_service(app_state, _config())

        assert app_state.slice(HrStateSlice).training_service is None


class TestAnUnanticipatedFailurePropagates:
    async def test_a_build_that_raises_is_not_turned_into_a_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A build that raises must propagate: swallowed, a real fault inside
        # the build is indistinguishable from "training is off", and the
        # deployment reads as configured rather than broken.
        app_state = _complete_app_state()

        def _explode(**kwargs: object) -> None:
            del kwargs
            raise TypeError

        monkeypatch.setattr(
            "synthorg.hr.training.factory.build_training_service",
            _explode,
        )

        with pytest.raises(TypeError):
            await wire_training_service(app_state, _config())

        assert app_state.slice(HrStateSlice).training_service is None


class TestTeardown:
    async def test_unwire_drops_the_service(self) -> None:
        # The extractors bake in the memory backend, so a replaced backend has
        # to reach this service; without the teardown it keeps reading through
        # the disconnected instance and keeps reporting itself up.
        app_state = _complete_app_state()
        await wire_training_service(app_state, _config())

        await unwire_training_service(app_state)

        assert app_state.slice(HrStateSlice).training_service is None

    async def test_a_pass_after_teardown_rebuilds_it(self) -> None:
        app_state = _complete_app_state()
        await wire_training_service(app_state, _config())
        first = app_state.slice(HrStateSlice).training_service
        await unwire_training_service(app_state)

        await wire_training_service(app_state, _config())

        rebuilt = app_state.slice(HrStateSlice).training_service
        assert rebuilt is not None
        assert rebuilt is not first
