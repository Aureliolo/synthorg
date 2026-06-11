"""Tests for the :class:`AppState` hot-swap seams.

The boot install and ``post_setup_reinit`` expose a small set of named
seams as thin shims over ``AppStateSliceMixin.wire``. Each shim composes
the owning feature slice: ``swap_*`` hot-replaces, ``set_*`` is
once-only, ``set_*_if_absent`` installs only when the slot is empty
(injection wins over autowire), and ``swap_notification_dispatcher``
returns the previous dispatcher so the caller can close its sinks.
Readers observe the result through the owning slice.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.pipeline.entry.protocol import WorkEntryAdapter
from synthorg.engine.pipeline.entry.task_board_adapter import TaskBoardEntryAdapter
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workspace.state import WorkspaceStateSlice, agent_workspace_root_of
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice
from synthorg.workers.execution_service import NoProviderExecutionService
from synthorg.workers.state import RuntimeStateSlice, worker_execution_service_of
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _make_state() -> AppState:
    """Build a bare thin ``AppState`` with no services wired."""
    return AppState(config=RootConfig(company_name="test"))


class TestProviderRegistrySwap:
    """``swap_provider_registry`` hot-replaces the providers-slice registry."""

    def test_swap_from_unset_attaches(self) -> None:
        state = _make_state()
        registry = ProviderRegistry({})
        state.swap_provider_registry(registry)
        assert state.slice(ProvidersStateSlice).registry is registry

    def test_swap_replaces_existing(self) -> None:
        state = _make_state()
        old = ProviderRegistry({})
        new = ProviderRegistry({})
        state.swap_provider_registry(old)
        state.swap_provider_registry(new)
        assert state.slice(ProvidersStateSlice).registry is new


class TestWorkerExecutionServiceSeam:
    """``set_worker_execution_service`` (once) / ``swap_worker_execution_service``."""

    def test_set_installs_once(self) -> None:
        state = _make_state()
        service = NoProviderExecutionService()
        state.set_worker_execution_service(service)
        assert state.slice(RuntimeStateSlice).worker_execution_service is service

    def test_set_twice_raises(self) -> None:
        state = _make_state()
        state.set_worker_execution_service(NoProviderExecutionService())
        with pytest.raises(RuntimeError, match="already configured"):
            state.set_worker_execution_service(NoProviderExecutionService())

    def test_swap_attaches_when_unset(self) -> None:
        state = _make_state()
        service = NoProviderExecutionService()
        state.swap_worker_execution_service(service)
        assert state.slice(RuntimeStateSlice).worker_execution_service is service

    def test_swap_replaces_existing(self) -> None:
        state = _make_state()
        first = NoProviderExecutionService()
        second = NoProviderExecutionService()
        state.set_worker_execution_service(first)
        state.swap_worker_execution_service(second)
        assert state.slice(RuntimeStateSlice).worker_execution_service is second

    def test_accessor_lazy_default_raises_without_task_engine(self) -> None:
        # The lazy ``LifecycleAdvancingExecutionService`` default needs a
        # task engine; a bare state has none, so the accessor 503s rather
        # than composing a half-built service.
        state = _make_state()
        with pytest.raises(ServiceUnavailableError):
            worker_execution_service_of(state)

    def test_accessor_returns_installed_service(self) -> None:
        state = _make_state()
        service = NoProviderExecutionService()
        state.set_worker_execution_service(service)
        assert worker_execution_service_of(state) is service


class TestCoordinatorSeam:
    """``set_coordinator_if_absent`` (injection wins) / ``swap_coordinator``."""

    def test_set_if_absent_installs_when_unset(self) -> None:
        state = _make_state()
        coordinator = mock_of[MultiAgentCoordinator]()
        state.set_coordinator_if_absent(coordinator)
        assert state.slice(RuntimeStateSlice).coordinator is coordinator

    def test_set_if_absent_keeps_injected(self) -> None:
        state = _make_state()
        injected = mock_of[MultiAgentCoordinator]()
        autowired = mock_of[MultiAgentCoordinator]()
        state.swap_coordinator(injected)
        state.set_coordinator_if_absent(autowired)
        # Injection-over-autowire: the already-wired coordinator wins.
        assert state.slice(RuntimeStateSlice).coordinator is injected

    def test_swap_replaces_existing(self) -> None:
        state = _make_state()
        first = mock_of[MultiAgentCoordinator]()
        second = mock_of[MultiAgentCoordinator]()
        state.swap_coordinator(first)
        state.swap_coordinator(second)
        assert state.slice(RuntimeStateSlice).coordinator is second


class TestWorkPipelineSeam:
    """``set_work_pipeline_if_absent`` (injection wins) / ``swap_work_pipeline``."""

    def test_set_if_absent_installs_when_unset(self) -> None:
        state = _make_state()
        pipeline = mock_of[WorkPipeline]()
        state.set_work_pipeline_if_absent(pipeline)
        assert state.slice(EngineStateSlice).work_pipeline is pipeline

    def test_set_if_absent_keeps_injected(self) -> None:
        state = _make_state()
        injected = mock_of[WorkPipeline]()
        autowired = mock_of[WorkPipeline]()
        state.swap_work_pipeline(injected)
        state.set_work_pipeline_if_absent(autowired)
        assert state.slice(EngineStateSlice).work_pipeline is injected

    def test_swap_replaces_existing(self) -> None:
        state = _make_state()
        first = mock_of[WorkPipeline]()
        second = mock_of[WorkPipeline]()
        state.swap_work_pipeline(first)
        state.swap_work_pipeline(second)
        assert state.slice(EngineStateSlice).work_pipeline is second


class TestEntryAdapterSeams:
    """The intake / objective / brownfield / task-board entry-adapter seams.

    All four share one contract (set-if-absent installs once, injection
    wins over a second set-if-absent, swap hot-replaces), differing only
    in the seam method names, the slice field, and the adapter type.
    """

    @pytest.mark.parametrize(
        ("set_if_absent", "swap", "field", "make_adapter"),
        [
            (
                "set_intake_entry_adapter_if_absent",
                "swap_intake_entry_adapter",
                "intake_entry_adapter",
                mock_of[WorkEntryAdapter],
            ),
            (
                "set_objective_entry_adapter_if_absent",
                "swap_objective_entry_adapter",
                "objective_entry_adapter",
                mock_of[WorkEntryAdapter],
            ),
            (
                "set_brownfield_entry_adapter_if_absent",
                "swap_brownfield_entry_adapter",
                "brownfield_entry_adapter",
                mock_of[WorkEntryAdapter],
            ),
            (
                "set_task_board_entry_adapter_if_absent",
                "swap_task_board_entry_adapter",
                "task_board_entry_adapter",
                mock_of[TaskBoardEntryAdapter],
            ),
        ],
    )
    def test_set_if_absent_then_swap(
        self,
        set_if_absent: str,
        swap: str,
        field: str,
        make_adapter: Callable[[], object],
    ) -> None:
        state = _make_state()
        first = make_adapter()
        second = make_adapter()
        getattr(state, set_if_absent)(first)
        assert getattr(state.slice(EngineStateSlice), field) is first
        # Injection wins: a second if-absent is a no-op.
        getattr(state, set_if_absent)(second)
        assert getattr(state.slice(EngineStateSlice), field) is first
        # Swap hot-replaces.
        getattr(state, swap)(second)
        assert getattr(state.slice(EngineStateSlice), field) is second


class TestNotificationDispatcherSwap:
    """``swap_notification_dispatcher`` returns the previously wired one."""

    def test_first_swap_returns_none(self) -> None:
        state = _make_state()
        first = NotificationDispatcher(sinks=())
        assert state.swap_notification_dispatcher(first) is None
        assert state.slice(NotificationsStateSlice).dispatcher is first

    def test_second_swap_returns_previous(self) -> None:
        state = _make_state()
        first = NotificationDispatcher(sinks=())
        second = NotificationDispatcher(sinks=())
        state.swap_notification_dispatcher(first)
        # The caller awaits ``aclose()`` on the returned previous one.
        assert state.swap_notification_dispatcher(second) is first
        assert state.slice(NotificationsStateSlice).dispatcher is second


class TestAgentWorkspaceRoot:
    """``agent_workspace_root_of`` default fallback + pinned-via-wire path."""

    def test_default_is_absolute_temp_path(self) -> None:
        state = _make_state()
        root = agent_workspace_root_of(state)
        assert root.is_absolute()
        assert "synthorg-agent-workspaces" in str(root)

    def test_pinned_root_is_returned(self, tmp_path: Path) -> None:
        state = _make_state()
        state.wire(WorkspaceStateSlice, agent_workspace_root=tmp_path)
        assert agent_workspace_root_of(state) == tmp_path
