"""Tests for ``GET /api/v1/capabilities``."""

from typing import Any

import pytest
from litestar.testing import TestClient

from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")


@pytest.mark.unit
class TestCapabilitiesController:
    """Capabilities endpoint reports which optional subsystems are wired."""

    def test_capabilities_endpoint_returns_full_flag_matrix(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/capabilities/",
            headers=_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        # Every documented flag is present and boolean.
        expected_flags = {
            "simulations",
            "requests",
            "ontology",
            "tunnel",
            "webhooks",
            "a2a",
            "telemetry",
            "integrations",
        }
        assert set(data.keys()) == expected_flags
        for key in expected_flags:
            assert isinstance(data[key], bool), key
        # The shared test app is built with a TaskEngine, so the
        # client-simulation runtime is boot-wired (DirectIntake +
        # InternalReviewStage); simulations + requests are therefore
        # on. ontology + tunnel are auto-wired on startup.
        assert data["simulations"] is True
        assert data["requests"] is True
        assert data["ontology"] is True
        assert data["tunnel"] is True
        assert data["webhooks"] is False
        assert data["a2a"] is False
        assert data["telemetry"] is False
        assert data["integrations"] is False

    def test_capabilities_reflects_wired_simulation_runtime(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """A TaskEngine-backed boot wires the simulation runtime.

        ``create_app`` builds the runtime via
        ``build_client_simulation_runtime`` whenever a TaskEngine is
        present (the shared fixture supplies one), so both the
        simulations and requests capability flags are True and the
        dashboard knows to poll those endpoints.
        """
        resp = test_client.get(
            "/api/v1/capabilities/",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["simulations"] is True
        assert data["requests"] is True

    def test_simulations_route_registered_when_runtime_wired(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """The simulations route is registered once the runtime is wired.

        With the boot-wired ``client_simulation_state`` the simulation
        controller is registered, so ``GET /api/v1/simulations``
        resolves (200 with an empty paginated list) instead of the
        404 returned when no TaskEngine gates the runtime off.
        """
        resp = test_client.get(
            "/api/v1/simulations",
            headers=_HEADERS,
        )
        assert resp.status_code == 200, resp.text

    def test_requests_route_registered_when_runtime_wired(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/requests",
            headers=_HEADERS,
        )
        assert resp.status_code == 200, resp.text
