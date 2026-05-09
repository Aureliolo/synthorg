"""Tests for the ``POST /meta/chat`` endpoint."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient

from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.models import ChatResponse
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.signals.service import SignalsService
from tests.unit.api.conftest import make_auth_headers

_BASE = "/api/v1/meta/chat"
_HEADERS = make_auth_headers("ceo")


def _empty_snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=7.0,
            avg_success_rate=0.8,
            avg_collaboration_score=6.5,
            agent_count=5,
        ),
        budget=OrgBudgetSummary(
            total_spend=10.0,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            forecast_confidence=0.8,
            orchestration_overhead=0.2,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


@pytest.mark.unit
class TestMetaChat:
    """Endpoint dispatches to ChiefOfStaffChat when wired, 503 otherwise."""

    def test_returns_503_when_chat_not_wired(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """No chief_of_staff_chat wired => explicit ServiceUnavailableError."""
        app_state = test_client.app.state.app_state
        original = app_state._chief_of_staff_chat
        app_state._chief_of_staff_chat = None
        try:
            resp = test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"question": "How are we doing?"},
            )
            assert resp.status_code == 503
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Service unavailable"
        finally:
            app_state._chief_of_staff_chat = original

    async def test_returns_chat_payload_when_wired(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Wired chat backend returns the answer + sources + confidence."""
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="Quality is up 5%.",
            sources=("performance",),
            confidence=0.8,
        )
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()

        app_state = test_client.app.state.app_state
        chat_original = app_state._chief_of_staff_chat
        signals_original = app_state._signals_service
        app_state._chief_of_staff_chat = chat_mock
        app_state._signals_service = signals_mock
        try:
            resp = test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"question": "How is quality trending?"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["answer"] == "Quality is up 5%."
            assert body["data"]["sources"] == ["performance"]
            assert body["data"]["confidence"] == pytest.approx(0.8)
        finally:
            app_state._chief_of_staff_chat = chat_original
            app_state._signals_service = signals_original

    def test_returns_503_when_signals_service_missing(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """A wired chat backend still 503s if SignalsService is unavailable."""
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        app_state = test_client.app.state.app_state
        chat_original = app_state._chief_of_staff_chat
        signals_original = app_state._signals_service
        app_state._chief_of_staff_chat = chat_mock
        app_state._signals_service = None
        try:
            resp = test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"question": "How are we doing?"},
            )
            assert resp.status_code == 503
            body = resp.json()
            assert body["success"] is False
            assert body["error"] == "Service unavailable"
        finally:
            app_state._chief_of_staff_chat = chat_original
            app_state._signals_service = signals_original
