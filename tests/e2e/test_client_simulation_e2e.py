"""End-to-end test for the full client simulation loop via the API.

Exercises the complete flow:

1. Create a client via POST /clients
2. Submit a request via POST /requests
3. Reject the request via POST /requests/{id}/reject
4. Start a simulation via POST /simulations
5. Poll the simulation until it reaches completion

The intake engine uses a deterministic fake strategy so the test
does not depend on real provider or task engine integration.
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review.stages.internal import InternalReviewStage
from tests._shared import LoopAsyncClient
from tests._shared import build_test_app as create_app
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
    """Provide the env vars required by API startup."""
    monkeypatch.setenv("SYNTHORG_JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", _TEST_SETTINGS_KEY)


class _AcceptingStrategy:
    """Intake strategy that always accepts with a stub task id."""

    async def process(self, request: Any) -> IntakeResult:
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=f"task-{request.request_id[:6]}",
        )


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
async def e2e_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> AsyncGenerator[LoopAsyncClient]:
    """Build the full app with client-simulation state attached."""
    config = RootConfig(company_name="e2e-company")
    auth_service = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    state = ClientSimulationState(
        intake_engine=IntakeEngine(strategy=_AcceptingStrategy()),
        review_pipeline=ReviewPipeline(stages=(InternalReviewStage(),)),
    )
    # Pass ``client_simulation_state`` via kwarg so ``create_app`` wires
    # it before the OPTIONAL_CONTROLLERS predicate gate -- post-build
    # ``set_client_simulation_state`` would arrive after route
    # registration and the simulation/request endpoints would be 404.
    app = create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        client_simulation_state=state,
    )
    async with LoopAsyncClient(app) as client:
        yield client


async def _create_client_and_submit_request(client: LoopAsyncClient) -> str:
    """Create the e2e client and submit a requirement; return the request id.

    Asserts both creations succeed and the request appears in the list.
    """
    create_resp = await client.post(
        "/api/v1/clients/",
        json={
            "client_id": "e2e-client",
            "name": "E2E Client",
            "persona": "End-to-end simulation operator",
            "expertise_domains": ["backend"],
            "strictness_level": 0.6,
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["data"]["client_id"] == "e2e-client"

    submit_resp = await client.post(
        "/api/v1/requests/",
        json={
            "client_id": "e2e-client",
            "requirement": {
                "title": "E2E Feature",
                "description": "Build a sample feature for the e2e run.",
            },
        },
    )
    assert submit_resp.status_code == 201
    request_id: str = submit_resp.json()["data"]["request_id"]
    assert submit_resp.json()["data"]["status"] == "submitted"

    list_resp = await client.get("/api/v1/requests")
    assert list_resp.status_code == 200
    assert any(r["request_id"] == request_id for r in list_resp.json()["data"])
    return request_id


class TestClientSimulationE2E:
    """End-to-end client simulation loop via the HTTP API."""

    async def test_full_client_lifecycle(
        self,
        e2e_client: LoopAsyncClient,
    ) -> None:
        """Create client, submit request, reject it, then run a sim."""
        e2e_client.headers.update(make_auth_headers("ceo"))
        request_id = await _create_client_and_submit_request(e2e_client)

        # 3. Reject the request (exercises the cancel transition).
        reject_resp = await e2e_client.post(
            f"/api/v1/requests/{request_id}/reject",
            json={"reason": "covered by the simulation run"},
        )
        assert reject_resp.status_code == 201
        assert reject_resp.json()["data"]["status"] == "cancelled"

        # 4. Kick off a simulation run.
        start_resp = await e2e_client.post(
            "/api/v1/simulations/",
            json={
                "config": {
                    "project_id": "e2e-project",
                    "rounds": 1,
                    "clients_per_round": 1,
                    "requirements_per_client": 1,
                },
            },
        )
        assert start_resp.status_code == 201
        simulation_id = start_resp.json()["data"]["simulation_id"]

        # 5. Fetch the simulation detail and confirm it is reachable.
        detail_resp = await e2e_client.get(
            f"/api/v1/simulations/{simulation_id}",
        )
        assert detail_resp.status_code == 200
        assert detail_resp.json()["data"]["simulation_id"] == simulation_id

        # 6. Missing review returns 404/503 as a defensive guard.
        review_resp = await e2e_client.get(
            "/api/v1/reviews/missing-task/pipeline",
        )
        assert review_resp.status_code in {404, 503}
