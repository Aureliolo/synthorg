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
        # Exact-value assertions against the test conftest's wiring.
        # The test fixture does NOT wire the optional subsystems
        # (client simulation state, telemetry collector, tunnel
        # provider) and ships with ``integrations.enabled=False``, so
        # each flag has a known value. A regression where the
        # controller hardcodes a flag to True (e.g. via a copy-paste
        # mistake) is caught here. ``ontology`` is wired automatically
        # in the on-startup phase whenever persistence is connected
        # (the test fixture connects an in-memory backend), so it
        # reads True.
        assert data["simulations"] is False
        assert data["requests"] is False
        assert data["ontology"] is True
        assert data["tunnel"] is False
        assert data["webhooks"] is False
        assert data["a2a"] is False
        assert data["telemetry"] is False
        assert data["integrations"] is False

    def test_capabilities_reflects_unconfigured_simulations(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Test conftest does not wire client_simulation_state."""
        resp = test_client.get(
            "/api/v1/capabilities/",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # client_simulation_state is not wired in the test fixture so
        # both the simulations and requests flags must be False -- the
        # dashboard then knows to skip polling those endpoints.
        assert data["simulations"] is False
        assert data["requests"] is False

    def test_simulations_route_returns_404_when_unconfigured(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Unconfigured simulations route does not exist.

        Without ``client_simulation_state`` wired the simulation
        controller is not registered at all, so the dashboard's
        polling against ``/api/v1/simulations`` lands at 404 (route
        not found) instead of 503 (service unavailable). Combined
        with the frontend reading ``/capabilities`` to gate polling
        in the first place, the 503-spam from the audit log is gone.
        """
        resp = test_client.get(
            "/api/v1/simulations",
            headers=_HEADERS,
        )
        assert resp.status_code == 404

    def test_requests_route_returns_404_when_unconfigured(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/requests",
            headers=_HEADERS,
        )
        assert resp.status_code == 404
