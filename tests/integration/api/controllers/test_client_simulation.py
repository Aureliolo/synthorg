"""Integration tests for client simulation controllers.

Covers ``/clients``, ``/requests``, ``/simulations``, ``/reviews``
endpoints using the in-memory fake persistence + message bus.
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.budget.tracker import CostTracker
from synthorg.client.adapters import DirectAdapter
from synthorg.client.pool import RoundRobinStrategy
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.enums import TaskStatus
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review.stages.internal import InternalReviewStage
from tests.unit.api.conftest import (
    _make_test_auth_service,
    _seed_test_users,
    make_auth_headers,
)
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration


_TEST_JWT_SECRET = "integration-test-secret-at-least-32-characters"
_TEST_SETTINGS_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.fixture(autouse=True)
def _required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required env vars for the API backend."""
    monkeypatch.setenv("SYNTHORG_JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("SYNTHORG_SETTINGS_KEY", _TEST_SETTINGS_KEY)


class _AcceptingStrategy:
    """Intake strategy that always accepts and emits a stub task id."""

    async def process(self, request):  # type: ignore[no-untyped-def]
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


def _build_sim_state() -> ClientSimulationState:
    """Construct a minimal client-simulation state for the test app."""
    return ClientSimulationState(
        intake_engine=IntakeEngine(strategy=_AcceptingStrategy()),
        review_pipeline=ReviewPipeline(stages=(InternalReviewStage(),)),
        intake_default_project="client-intake",
    )


class _StubEntryAdapter:
    """Work-entry adapter double that returns a fixed pipeline result."""

    @property
    def source(self) -> WorkSource:
        return WorkSource.INTAKE

    async def submit(self, request: Any) -> WorkPipelineResult:
        item = WorkItem(
            origin_adapter_id="intake-entry-adapter",
            source=WorkSource.INTAKE,
            title=request.requirement.title,
            raw_intent=request.requirement.description,
            project="client-intake",
            requested_by=request.client_id,
            correlation_id=request.request_id,
        )
        return WorkPipelineResult(
            work_item=item,
            verdict=RoutingVerdict.LEAF,
            execution_path=ExecutionPath.SOLO,
            task_id=f"task-{request.request_id[:6]}",
            final_task_status=TaskStatus.IN_REVIEW,
            phases=(
                WorkPhaseResult(phase="intake", success=True, duration_seconds=0.0),
            ),
            total_duration_seconds=0.0,
        )


def _build_client_with_adapter(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> tuple[TestClient[Any], ClientSimulationState]:
    config = RootConfig(company_name="test")
    auth_service = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    sim_state = _build_sim_state()
    app = create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        client_simulation_state=sim_state,
        intake_entry_adapter=_StubEntryAdapter(),
    )
    return TestClient(app), sim_state


def _build_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> TestClient[Any]:
    config = RootConfig(company_name="test")
    auth_service = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    # Pass ``client_simulation_state`` directly so ``create_app`` wires
    # it onto AppState before the OPTIONAL_CONTROLLERS predicate check;
    # post-construction ``set_client_simulation_state`` would land
    # AFTER the controller list is frozen and the routes would be
    # absent under conditional registration.
    app = create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        client_simulation_state=_build_sim_state(),
    )
    return TestClient(app)


class TestClientController:
    async def test_create_list_delete_cycle(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            create_resp = client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "c-1",
                    "name": "Alice",
                    "persona": "Detail-oriented reviewer",
                    "expertise_domains": ["backend", "security"],
                    "strictness_level": 0.7,
                },
            )
            assert create_resp.status_code == 201
            assert create_resp.json()["data"]["client_id"] == "c-1"

            list_resp = client.get("/api/v1/clients")
            assert list_resp.status_code == 200
            body = list_resp.json()
            assert len(body["data"]) == 1

            get_resp = client.get("/api/v1/clients/c-1")
            assert get_resp.status_code == 200
            assert get_resp.json()["data"]["name"] == "Alice"

            delete_resp = client.delete("/api/v1/clients/c-1")
            assert delete_resp.status_code == 204
            final = client.get("/api/v1/clients")
            assert len(final.json()["data"]) == 0

    async def test_duplicate_client_id_conflict(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            payload = {
                "client_id": "dup",
                "name": "Dup",
                "persona": "Persona",
            }
            first = client.post("/api/v1/clients/", json=payload)
            assert first.status_code == 201
            second = client.post("/api/v1/clients/", json=payload)
            assert second.status_code == 409

    async def test_get_missing_returns_404(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = client.get("/api/v1/clients/missing")
            assert resp.status_code == 404

    async def test_update_patches_fields(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "patch",
                    "name": "Original",
                    "persona": "Persona",
                },
            )
            resp = client.patch(
                "/api/v1/clients/patch",
                json={"name": "Renamed", "strictness_level": 0.9},
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["name"] == "Renamed"
            assert resp.json()["data"]["strictness_level"] == 0.9


class TestRequestController:
    async def test_submit_stores_request(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "client-req",
                    "name": "Req Client",
                    "persona": "Persona",
                },
            )
            submit = client.post(
                "/api/v1/requests/",
                json={
                    "client_id": "client-req",
                    "requirement": {
                        "title": "Do the thing",
                        "description": "Build the feature end to end.",
                    },
                },
            )
            assert submit.status_code == 201
            assert submit.json()["data"]["status"] == "submitted"

            listing = client.get("/api/v1/requests")
            assert listing.status_code == 200
            assert len(listing.json()["data"]) == 1

    async def test_approve_without_adapter_returns_409(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """No work-entry adapter wired -> honest runtime-not-configured."""
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={"client_id": "na", "name": "NA", "persona": "P"},
            )
            submit = client.post(
                "/api/v1/requests/",
                json={
                    "client_id": "na",
                    "requirement": {"title": "T", "description": "D"},
                },
            )
            rid = submit.json()["data"]["request_id"]
            approve = client.post(f"/api/v1/requests/{rid}/approve")
            assert approve.status_code == 409

    async def test_approve_accepts_and_spawns_pipeline(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Approve returns 202 APPROVED and spawns the pipeline task."""
        client, sim_state = _build_client_with_adapter(
            fake_persistence,
            fake_message_bus,
        )
        with client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={"client_id": "ok", "name": "OK", "persona": "P"},
            )
            submit = client.post(
                "/api/v1/requests/",
                json={
                    "client_id": "ok",
                    "requirement": {"title": "T", "description": "D"},
                },
            )
            rid = submit.json()["data"]["request_id"]
            approve = client.post(f"/api/v1/requests/{rid}/approve")
            assert approve.status_code == 202
            assert approve.json()["data"]["status"] == "approved"
            # The reconciliation task was registered synchronously
            # before the response returned (strong ref held).
            assert len(sim_state.background_tasks) >= 1

    async def test_reject_sets_cancelled(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "rj",
                    "name": "Reject",
                    "persona": "Persona",
                },
            )
            submit = client.post(
                "/api/v1/requests/",
                json={
                    "client_id": "rj",
                    "requirement": {
                        "title": "Something",
                        "description": "Description",
                    },
                },
            )
            rid = submit.json()["data"]["request_id"]
            reject = client.post(
                f"/api/v1/requests/{rid}/reject",
                json={"reason": "out of scope"},
            )
            assert reject.status_code == 201
            assert reject.json()["data"]["status"] == "cancelled"

    async def test_submit_unknown_client_404(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            resp = client.post(
                "/api/v1/requests/",
                json={
                    "client_id": "missing",
                    "requirement": {
                        "title": "Title",
                        "description": "Description",
                    },
                },
            )
            assert resp.status_code == 404


class TestSimulationController:
    async def test_start_simulation_returns_running(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "sim",
                    "name": "Sim",
                    "persona": "Persona",
                },
            )
            resp = client.post(
                "/api/v1/simulations/",
                json={
                    "config": {
                        "project_id": "proj-1",
                        "rounds": 1,
                        "clients_per_round": 1,
                        "requirements_per_client": 1,
                    },
                },
            )
            assert resp.status_code == 201
            sid = resp.json()["data"]["simulation_id"]
            list_resp = client.get("/api/v1/simulations")
            assert list_resp.status_code == 200
            assert len(list_resp.json()["data"]) == 1
            detail = client.get(f"/api/v1/simulations/{sid}")
            assert detail.status_code == 200

    async def test_get_missing_simulation_returns_404(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = client.get("/api/v1/simulations/missing")
            assert resp.status_code == 404

    async def test_start_simulation_duplicate_id_returns_409(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """A second start with the same simulation_id is rejected."""
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "sim",
                    "name": "Sim",
                    "persona": "Persona",
                },
            )
            payload = {
                "config": {
                    "simulation_id": "fixed-sim-001",
                    "project_id": "proj-1",
                    "rounds": 1,
                    "clients_per_round": 1,
                    "requirements_per_client": 1,
                },
            }
            first = client.post("/api/v1/simulations/", json=payload)
            assert first.status_code == 201
            second = client.post("/api/v1/simulations/", json=payload)
            assert second.status_code == 409


class TestReviewController:
    async def test_missing_task_returns_404(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = client.get("/api/v1/reviews/missing-task/pipeline")
            # task_engine may not be available in this minimal test app,
            # so either 404 or 503 is acceptable as a defensive response.
            assert resp.status_code in {404, 503}

    async def test_decide_stage_missing_task_returns_4xx(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            resp = client.post(
                "/api/v1/reviews/missing-task/stages/internal/decide",
                json={
                    "verdict": "pass",
                    "reason": "manual override",
                },
            )
            # Without a task_engine the lookup may 404/503 instead
            # of producing a decision. Accept any 4xx/5xx defensive
            # response here; the happy path is exercised via
            # dedicated unit tests for the review controller.
            # ``StageDecisionPayload`` enforces ``extra="forbid"``, so
            # the body sent above only carries the fields the model
            # declares; a Pydantic-level rejection would otherwise
            # surface as 400 and mask the missing-task path that this
            # smoke test exists to assert.
            assert resp.status_code in {404, 409, 503}


class TestSatisfactionEndpoint:
    async def test_empty_history_for_new_client(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "sat-1",
                    "name": "Satisfaction",
                    "persona": "Persona",
                },
            )
            resp = client.get("/api/v1/clients/sat-1/satisfaction")
            assert resp.status_code == 200
            body = resp.json()["data"]
            assert body["client_id"] == "sat-1"
            assert body["total_reviews"] == 0
            assert body["acceptance_rate"] == 0.0
            assert body["history"] == []

    async def test_missing_client_returns_404(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("observer"))
            resp = client.get("/api/v1/clients/missing/satisfaction")
            assert resp.status_code == 404


class TestScopeEndpoint:
    async def test_scope_submitted_request(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "scp",
                    "name": "Scope",
                    "persona": "Persona",
                },
            )
            submit = client.post(
                "/api/v1/requests/",
                json={
                    "client_id": "scp",
                    "requirement": {
                        "title": "Initial",
                        "description": "Initial description",
                    },
                },
            )
            rid = submit.json()["data"]["request_id"]
            scope = client.post(
                f"/api/v1/requests/{rid}/scope",
                json={
                    "notes": "Scoped by PM",
                    "refined_title": "Refined",
                },
            )
            assert scope.status_code == 201
            body = scope.json()["data"]
            assert body["status"] == "scoping"
            assert body["requirement"]["title"] == "Refined"
            assert body["metadata"]["scoping_notes"] == "Scoped by PM"

    async def test_scope_wrong_status_conflict(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "scp2",
                    "name": "Scope2",
                    "persona": "Persona",
                },
            )
            submit = client.post(
                "/api/v1/requests/",
                json={
                    "client_id": "scp2",
                    "requirement": {
                        "title": "Title",
                        "description": "Description",
                    },
                },
            )
            rid = submit.json()["data"]["request_id"]
            client.post(
                f"/api/v1/requests/{rid}/reject",
                json={"reason": "drop"},
            )
            resp = client.post(
                f"/api/v1/requests/{rid}/scope",
                json={"notes": "too late"},
            )
            assert resp.status_code == 409


class TestSimulationReportEndpoint:
    async def test_summary_report_for_known_run(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "rpt",
                    "name": "Report",
                    "persona": "Persona",
                },
            )
            resp = client.post(
                "/api/v1/simulations/",
                json={
                    "config": {
                        "project_id": "proj-rpt",
                        "rounds": 1,
                        "clients_per_round": 1,
                        "requirements_per_client": 1,
                    },
                },
            )
            sid = resp.json()["data"]["simulation_id"]
            report = client.get(f"/api/v1/simulations/{sid}/report")
            assert report.status_code == 200
            payload = report.json()["data"]
            assert payload["format"] == "summary"
            assert payload["simulation_id"] == sid
            assert "totals" in payload
            assert "rates" in payload

    async def test_unknown_format_returns_409(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "rpt2",
                    "name": "Report2",
                    "persona": "Persona",
                },
            )
            resp = client.post(
                "/api/v1/simulations/",
                json={
                    "config": {
                        "project_id": "proj-rpt2",
                        "rounds": 1,
                        "clients_per_round": 1,
                        "requirements_per_client": 1,
                    },
                },
            )
            sid = resp.json()["data"]["simulation_id"]
            report = client.get(
                f"/api/v1/simulations/{sid}/report?fmt=bogus",
            )
            assert report.status_code == 409


class TestSimulationCancel:
    async def test_cancel_running_simulation(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "cxl",
                    "name": "Cancel",
                    "persona": "Persona",
                },
            )
            start = client.post(
                "/api/v1/simulations/",
                json={
                    "config": {
                        "project_id": "proj-cxl",
                        "rounds": 1,
                        "clients_per_round": 1,
                        "requirements_per_client": 1,
                    },
                },
            )
            sid = start.json()["data"]["simulation_id"]
            resp = client.post(f"/api/v1/simulations/{sid}/cancel")
            # Either cancels the run or reports already-terminal
            # (the background task may finish before the cancel
            # lands in fast paths).
            assert resp.status_code in {200, 409}


class TestClientDeactivation:
    async def test_deactivate_removes_from_list(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        with _build_client(fake_persistence, fake_message_bus) as client:
            client.headers.update(make_auth_headers("ceo"))
            client.post(
                "/api/v1/clients/",
                json={
                    "client_id": "deact",
                    "name": "Deact",
                    "persona": "Persona",
                },
            )
            resp = client.delete("/api/v1/clients/deact")
            assert resp.status_code == 204
            listing = client.get("/api/v1/clients")
            assert all(row["client_id"] != "deact" for row in listing.json()["data"])
            satisfaction = client.get("/api/v1/clients/deact/satisfaction")
            assert satisfaction.status_code == 200


# Keep unused symbols referenced so linters see the fact that the
# test harness exercises these adapters through the simulation state.
_SENTINEL: tuple[type, ...] = (DirectAdapter, RoundRobinStrategy)
