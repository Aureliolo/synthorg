"""The tail's own activation path, driven with dependencies arriving late.

Every other test composes the tail through the rollup's constructor, which is
the exact shape that hid the original defect: the wiring was never executed, so
a rollup that came up before any provider existed reported a live tail forever
and no stage was ever attached.

These drive the shipped ``attach_*`` activations, in the order a real boot
resolves their dependencies, and assert the two properties the split exists
for: each collaborator comes up on its own dependency rather than on the union
of all of them, and one that attaches later is still seen by the stage built
before it.
"""

import pytest

from synthorg.api.lifecycle_helpers.project_rollup_wiring import (
    attach_evaluation_stage,
    attach_integration_stage,
    attach_replan_trigger,
    attach_ship_retro_capture,
    detach_ship_retro_capture,
    wire_project_rollup_service,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.communication.bus_protocol import MessageBus
from synthorg.config.schema import RootConfig
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.state import HrStateSlice
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.state import MemoryStateSlice
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.settings.state import SettingsStateSlice
from synthorg.workers.state import RuntimeStateSlice
from tests._shared import StubWorkPipeline, make_app_state, mock_of
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.integration


def _rollup(app_state: AppState) -> ProjectRollupService:
    """Read the wired rollup out of the engine slice.

    Returns:
        The rollup service, which every assertion here probes.
    """
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    assert rollup is not None
    return rollup


async def _booted() -> AppState:
    """Bring up the state a first boot has: persistence and the task engine.

    Returns:
        The app state, so a test can wire the later dependencies in whatever
        order it is exercising.
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    engine = TaskEngine(
        config=TaskEngineConfig(),
        persistence=backend,
        message_bus=mock_of[MessageBus](),
    )
    app_state = make_app_state()
    app_state.wire(PersistenceStateSlice, backend=backend)
    app_state.wire(EngineStateSlice, task_engine=engine)
    app_state.wire(
        SettingsStateSlice,
        config_resolver=ConfigResolver(
            settings_service=mock_of[SettingsServiceProtocol](),
            config=RootConfig(company_name="test"),
        ),
    )
    await wire_project_rollup_service(app_state)
    return app_state


def _wire_judgement_dependencies(app_state: AppState) -> None:
    """Wire what the EVALUATE stage needs, and nothing the others do."""
    app_state.wire(ProvidersStateSlice, registry=ProviderRegistry(drivers={}))
    app_state.wire(HrStateSlice, agent_registry=mock_of[AgentRegistryService]())


def _wire_memory_layers(app_state: AppState) -> None:
    """Wire both memory layers the retrospective capture writes into."""
    app_state.wire(
        MemoryStateSlice,
        backend=mock_of[MemoryBackend](),
        org_memory_backend=mock_of[OrgMemoryBackend](),
    )
    app_state.wire(BudgetStateSlice, cost_tracker=CostTracker())


class TestInitiativeTailActivation:
    async def test_the_first_boot_wires_a_tailless_rollup(self) -> None:
        """No provider, no pipeline, no coordinator: nothing to attach yet."""
        rollup = _rollup(await _booted())

        assert not rollup.has_integration()
        assert not rollup.has_evaluation()
        assert not rollup.has_replan_trigger()

    async def test_an_activation_without_its_dependency_names_what_is_missing(
        self,
    ) -> None:
        """Declining is the honest outcome, and it must name its own condition.

        A stage that installed a half-built collaborator would read as
        converged and the reconciler would never revisit it. One that backed
        out silently left ``GET /subsystems`` with nothing to report but
        "declined on a condition it does not declare".
        """
        app_state = await _booted()

        with pytest.raises(SubsystemDeclinedError, match="work pipeline"):
            await attach_integration_stage(app_state)
        with pytest.raises(SubsystemDeclinedError, match="provider"):
            await attach_evaluation_stage(app_state)
        with pytest.raises(SubsystemDeclinedError, match="coordinator"):
            await attach_replan_trigger(app_state)

        rollup = _rollup(app_state)
        assert not rollup.has_integration()
        assert not rollup.has_evaluation()
        assert not rollup.has_replan_trigger()

    async def test_the_work_pipeline_arriving_brings_up_integrate_alone(
        self,
    ) -> None:
        """The degradation table: a boot with no coordinator still integrates."""
        app_state = await _booted()
        app_state.wire(EngineStateSlice, work_pipeline=StubWorkPipeline())

        await attach_integration_stage(app_state)
        with pytest.raises(SubsystemDeclinedError, match="provider"):
            await attach_evaluation_stage(app_state)
        with pytest.raises(SubsystemDeclinedError, match="coordinator"):
            await attach_replan_trigger(app_state)

        rollup = _rollup(app_state)
        assert rollup.has_integration()
        assert not rollup.has_evaluation()
        assert not rollup.has_replan_trigger()

    async def test_a_trigger_attaching_later_is_seen_by_the_built_stage(
        self,
    ) -> None:
        """The EVALUATE stage reads the trigger per verdict, not at build time.

        The two are separate subsystems resolving on their own schedules, so a
        coordinator that arrives after the provider registry would otherwise
        leave the stage holding the ``None`` it was built with, and every unmet
        initiative parked instead of replanned.
        """
        app_state = await _booted()
        _wire_judgement_dependencies(app_state)

        await attach_evaluation_stage(app_state)
        rollup = _rollup(app_state)
        assert rollup.has_evaluation()
        assert rollup.replan_trigger() is None

        app_state.wire(RuntimeStateSlice, coordinator=mock_of[MultiAgentCoordinator]())
        await attach_replan_trigger(app_state)

        assert rollup.has_replan_trigger()
        assert rollup.replan_trigger() is not None

    async def test_the_retrospective_needs_memory_and_comes_down_again(self) -> None:
        """The retro tail is probed on its own, so drive it on its own.

        Its dependencies are the two memory layers, which converge on a
        different schedule from the provider registry. A tail that came up
        while memory was blocked would otherwise read as converged with the
        retrospective silently never firing, and the detach is what the
        reconciler reads as the subsystem being down.
        """
        app_state = await _booted()
        _wire_judgement_dependencies(app_state)
        rollup = _rollup(app_state)

        # The decline names which layer is missing, not merely that memory is
        # unconverged: that specific reason is what `GET /subsystems` reports.
        with pytest.raises(
            SubsystemDeclinedError,
            match="agent memory and org memory absent",
        ):
            await attach_ship_retro_capture(app_state)
        assert not rollup.has_retro_capture()

        _wire_memory_layers(app_state)
        await attach_ship_retro_capture(app_state)
        assert rollup.has_retro_capture()

        await detach_ship_retro_capture(app_state)
        assert not rollup.has_retro_capture()
