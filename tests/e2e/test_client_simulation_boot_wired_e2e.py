"""Acceptance: the boot-wired client-simulation runtime, end to end.

Two layers:

* HTTP: ``create_app`` with a ``TaskEngine`` and NO
  ``client_simulation_state`` kwarg boot-wires the real runtime, so
  ``/capabilities`` reports the subsystem on and the controllers
  register (the empty-company ``direct`` path, no provider).
* Harness: the ``SimulationRunner`` is driven directly against the
  runtime that ``build_client_simulation_runtime`` produces (the exact
  object ``create_app`` attaches), proving generated requirements flow
  through the real ``IntakeEngine`` into the real ``TaskEngine`` with
  deterministic, asserted metrics and zero real LLM spend -- under
  both the ``direct`` and the scripted ``agent`` strategy. Driving the
  runner directly (rather than the fire-and-forget HTTP background
  task) keeps the metric assertion deterministic and non-flaky.

The kwarg-override path stays covered by
``tests/e2e/test_client_simulation_e2e.py``.
"""

from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.budget.tracker import CostTracker
from synthorg.client.ai_client import AIClient
from synthorg.client.config import SimulationRunnerConfig
from synthorg.client.feedback.binary import BinaryFeedback
from synthorg.client.generators.procedural import ProceduralGenerator
from synthorg.client.models import ClientProfile, SimulationConfig
from synthorg.client.protocols import ClientInterface
from synthorg.client.runner import SimulationRunner
from synthorg.client.runtime_builder import build_client_simulation_runtime
from synthorg.config.schema import RootConfig
from synthorg.engine.intake.strategies import AgentIntake, DirectIntake
from synthorg.engine.task_engine import TaskEngine
from synthorg.providers.drivers.scripted import ScriptedDriver, SingleResponseStrategy
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.registry import ProviderRegistry
from tests._shared import make_app_state
from tests.unit.api.conftest import (
    _make_test_auth_service,
    _seed_test_users,
    make_auth_headers,
)
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.e2e

_TEST_JWT_SECRET = "integration-test-secret-at-least-32-characters"
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.fixture(autouse=True)
def _required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTHORG_JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", _TEST_SETTINGS_KEY)


@pytest.fixture
async def fake_persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def fake_message_bus() -> AsyncGenerator[FakeMessageBus]:
    bus = FakeMessageBus()
    await bus.start()
    yield bus
    await bus.stop()


@pytest.fixture
async def task_engine(
    fake_persistence: FakePersistenceBackend,
) -> AsyncGenerator[TaskEngine]:
    engine = TaskEngine(persistence=fake_persistence)
    await engine.start()
    yield engine
    await engine.stop()


def _deterministic_client(client_id: str) -> ClientInterface:
    """A no-LLM client: procedural generator + binary feedback."""
    profile = ClientProfile(
        client_id=client_id,
        name="Boot Client",
        persona="Deterministic boot-wired simulation operator",
        expertise_domains=("backend",),
        strictness_level=0.5,
    )
    return AIClient(
        profile=profile,
        generator=ProceduralGenerator(seed=7),
        feedback=BinaryFeedback(client_id=client_id),
    )


def _accepting_scripted_provider() -> ScriptedDriver:
    """Scripted provider whose every completion accepts the request."""
    response = CompletionResponse(
        content='{"accepted": true}',
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model="test-model-001",
    )
    return ScriptedDriver(
        "test-provider",
        strategy=SingleResponseStrategy(response=response),
    )


_SIM_CONFIG = SimulationConfig(
    simulation_id="boot-sim",
    project_id="boot-project",
    rounds=2,
    clients_per_round=1,
    requirements_per_client=3,
)


@pytest.fixture
def direct_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    task_engine: TaskEngine,
) -> Generator[TestClient[Any]]:
    """Boot-wired app, default ``direct`` intake (no provider)."""
    auth_service = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    app = create_app(
        config=RootConfig(company_name="boot-direct"),
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        task_engine=task_engine,
    )
    with TestClient(app) as client:
        yield client


class TestBootWiredHttpSurface:
    """The HTTP surface reflects the boot-wired runtime."""

    def test_capabilities_and_routes_on(
        self,
        direct_client: TestClient[Any],
    ) -> None:
        headers = make_auth_headers("ceo")
        caps = direct_client.get("/api/v1/capabilities/", headers=headers)
        assert caps.status_code == 200, caps.text
        data = caps.json()["data"]
        assert data["simulations"] is True
        assert data["requests"] is True
        # Routes are registered (200, not the 404 of an unwired runtime).
        assert (
            direct_client.get("/api/v1/simulations", headers=headers).status_code == 200
        )
        assert direct_client.get("/api/v1/requests", headers=headers).status_code == 200


class TestBootWiredDirectIntakeHarness:
    """``SimulationRunner`` against the boot-wired ``direct`` runtime."""

    async def test_run_drives_requirements_into_real_task_engine(
        self,
        task_engine: TaskEngine,
    ) -> None:
        app_state = make_app_state(
            task_engine=task_engine,
        )
        state = build_client_simulation_runtime(app_state, env={})
        assert state.intake_engine is not None
        assert isinstance(state.intake_engine.strategy, DirectIntake)

        runner = SimulationRunner(
            config=SimulationRunnerConfig(),
            intake_engine=state.intake_engine,
        )
        metrics, _ = await runner.run(
            sim_config=_SIM_CONFIG,
            clients=(_deterministic_client("boot-client"),),
        )

        # 2 rounds x 1 client -> 1 requirement per round (AIClient yields
        # the first generated requirement). DirectIntake accepts every
        # request, so every requirement becomes a real task; the review
        # stage then runs on each created task.
        assert metrics.total_requirements == 2, (
            f"expected 2 requirements (2 rounds x 1 client), got "
            f"{metrics.total_requirements}"
        )
        assert metrics.total_tasks_created == 2, (
            f"intake should create a task per requirement, got "
            f"{metrics.total_tasks_created}"
        )
        assert metrics.tasks_accepted + metrics.tasks_rejected == 2, (
            f"every created task should be reviewed, got "
            f"{metrics.tasks_accepted} accepted + {metrics.tasks_rejected} "
            f"rejected"
        )


class TestBootWiredAgentIntakeHarness:
    """``SimulationRunner`` against the boot-wired scripted ``agent``."""

    async def test_scripted_agent_intake_accepts_deterministically(
        self,
        task_engine: TaskEngine,
    ) -> None:
        registry = ProviderRegistry(
            {"test-provider": _accepting_scripted_provider()},
        )
        app_state = make_app_state(
            task_engine=task_engine,
            provider_registry=registry,
        )
        state = build_client_simulation_runtime(
            app_state,
            env={
                "SYNTHORG_SIMULATIONS_INTAKE_STRATEGY": "agent",
                "SYNTHORG_SIMULATIONS_INTAKE_MODEL": "test-model-001",
            },
        )
        assert state.intake_engine is not None
        assert isinstance(state.intake_engine.strategy, AgentIntake)

        runner = SimulationRunner(
            config=SimulationRunnerConfig(),
            intake_engine=state.intake_engine,
        )
        metrics, _ = await runner.run(
            sim_config=_SIM_CONFIG,
            clients=(_deterministic_client("agent-client"),),
        )

        # The scripted provider returns ``{"accepted": true}`` for every
        # triage call, so AgentIntake accepts every requirement and
        # creates a task -- deterministic, zero real LLM spend.
        assert metrics.total_requirements == 2, (
            f"expected 2 requirements (2 rounds x 1 client), got "
            f"{metrics.total_requirements}"
        )
        assert metrics.total_tasks_created == 2, (
            f"intake should create a task per requirement, got "
            f"{metrics.total_tasks_created}"
        )
        assert metrics.tasks_accepted + metrics.tasks_rejected == 2, (
            f"every created task should be reviewed, got "
            f"{metrics.tasks_accepted} accepted + {metrics.tasks_rejected} "
            f"rejected"
        )
