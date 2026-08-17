"""Tests for ``GET /api/v1/capabilities``."""

from typing import Protocol

import pytest

from synthorg.tools.web.readiness import WebResearchReadiness, WebSearchBlocker
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")


class _FixedResolver(Protocol):
    """The call shape the controller uses to reach readiness."""

    async def __call__(
        self,
        resolver: object,
        *,
        connections: object,
    ) -> WebResearchReadiness:
        """Answer with a fixed verdict."""
        ...


def _returning(readiness: WebResearchReadiness) -> _FixedResolver:
    """Build a stand-in resolver that answers with a fixed verdict.

    Substituted at the controller rather than driven through settings: what is
    under test here is that the controller reports the verdict faithfully, and
    reaching that state through the settings backend would test the resolver
    a second time instead.

    Returns:
        A coroutine function answering with *readiness* whatever it is passed.
    """

    async def _resolve(
        resolver: object,
        *,
        connections: object,
    ) -> WebResearchReadiness:
        del resolver, connections
        return readiness

    return _resolve


@pytest.mark.unit
class TestCapabilitiesController:
    """Capabilities endpoint reports which optional subsystems are wired."""

    async def test_capabilities_endpoint_returns_full_flag_matrix(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
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
            "web_search",
            "web_search_notify",
            "web_fetch",
        }
        # Web search reports WHY it is not up as well as whether it is, so the
        # dashboard can tell "off by choice" from "on but unusable".
        expected_reasons = {"web_search_blocker", "web_search_message"}
        # A blocked setup is told which credential it already holds, so it is
        # never asked for one that is sitting in the connection catalog.
        expected_lists = {"web_search_reusable_connections"}
        assert set(data.keys()) == expected_flags | expected_reasons | expected_lists
        for key in expected_flags:
            assert isinstance(data[key], bool), key
        for key in expected_reasons:
            assert isinstance(data[key], str), key
        for key in expected_lists:
            assert isinstance(data[key], list), key
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
        # Web search ships off, and off-by-choice is not a fault to report, so
        # there is nothing to raise with the operator either.
        assert data["web_search"] is False
        assert data["web_search_blocker"] == "disabled"
        assert data["web_search_message"] == ""
        assert data["web_search_notify"] is False
        assert data["web_search_reusable_connections"] == []
        # Fetch needs no credential, so it is on out of the box.
        assert data["web_fetch"] is True

    async def test_an_enabled_but_unconfigured_search_is_reported(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The whole point of the surface: on-but-unusable must be visible.

        The default boot has search off, which is not a fault and reports
        nothing, so without driving the misconfigured state the controller's
        plumbing of the blocker, the notice flag and the reuse suggestion is
        never exercised at all.
        """
        readiness = WebResearchReadiness(
            search_ready=False,
            search_blocker=WebSearchBlocker.NO_CONNECTION,
            provider_id="test-provider",
            fetch_enabled=True,
            reusable_connections=("saved-key",),
        )
        monkeypatch.setattr(
            "synthorg.api.controllers.capabilities.resolve_web_research_readiness",
            _returning(readiness),
        )
        resp = await async_test_client.get("/api/v1/capabilities/", headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["web_search"] is False
        assert data["web_search_blocker"] == "no_connection"
        assert "tools.web_search_connection" in data["web_search_message"]
        assert data["web_search_notify"] is True
        assert data["web_search_reusable_connections"] == ["saved-key"]

    async def test_a_dismissed_notice_stops_notifying_but_still_reports_blocked(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dismissal silences the notice and changes nothing else."""
        readiness = WebResearchReadiness(
            search_ready=False,
            search_blocker=WebSearchBlocker.NO_PROVIDER,
            fetch_enabled=True,
            notice_dismissed=True,
        )
        monkeypatch.setattr(
            "synthorg.api.controllers.capabilities.resolve_web_research_readiness",
            _returning(readiness),
        )
        resp = await async_test_client.get("/api/v1/capabilities/", headers=_HEADERS)
        data = resp.json()["data"]
        assert data["web_search"] is False
        assert data["web_search_blocker"] == "no_provider"
        assert data["web_search_notify"] is False

    async def test_capabilities_reflects_wired_simulation_runtime(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """A TaskEngine-backed boot wires the simulation runtime.

        ``create_app`` builds the runtime via
        ``build_client_simulation_runtime`` whenever a TaskEngine is
        present (the shared fixture supplies one), so both the
        simulations and requests capability flags are True and the
        dashboard knows to poll those endpoints.
        """
        resp = await async_test_client.get(
            "/api/v1/capabilities/",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["simulations"] is True
        assert data["requests"] is True

    async def test_simulations_route_registered_when_runtime_wired(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """The simulations route is registered once the runtime is wired.

        With the boot-wired ``client_simulation_state`` the simulation
        controller is registered, so ``GET /api/v1/simulations``
        resolves (200 with an empty paginated list) instead of the
        404 returned when no TaskEngine gates the runtime off.
        """
        resp = await async_test_client.get(
            "/api/v1/simulations",
            headers=_HEADERS,
        )
        assert resp.status_code == 200, resp.text

    async def test_requests_route_registered_when_runtime_wired(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/requests",
            headers=_HEADERS,
        )
        assert resp.status_code == 200, resp.text
