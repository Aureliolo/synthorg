"""Tests for the ``POST /meta/chat`` endpoint."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.models import ChatQuery, ChatResponse, CitedRecord
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
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.enums import SettingSource
from tests._shared import LoopAsyncClient, sid
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

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


class TestMetaChat:
    """Endpoint dispatches to ChiefOfStaffChat when wired, 503 otherwise."""

    async def test_returns_503_when_chat_not_wired(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """No chief_of_staff_chat wired => explicit ServiceUnavailableError."""
        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, chief_of_staff_chat=None)
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"question": "How are we doing?"},
            )
            assert resp.status_code == 503
            body = resp.json()
            assert body["success"] is False
            # The 503 surfaces the real reason (which dependency is missing)
            # rather than a bare "Service unavailable" placeholder.
            assert body["error"].startswith("ServiceUnavailableError:")
        finally:
            app_state.swap_slice(original_slice)

    async def test_returns_chat_payload_when_wired(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Wired chat backend returns the answer + sources + confidence."""
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        proposal_id = "11111111-1111-1111-1111-111111111111"
        alert_id = "22222222-2222-2222-2222-222222222222"
        chat_mock.ask.return_value = ChatResponse(
            answer="Quality is up 5%.",
            sources=("performance",),
            confidence=0.8,
        )
        signals_mock = AsyncMock(spec=SignalsService)
        expected_snapshot = _empty_snapshot()
        signals_mock.get_org_snapshot.return_value = expected_snapshot

        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
        )
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={
                    "question": "How is quality trending?",
                    "proposal_id": proposal_id,
                    "alert_id": alert_id,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["answer"] == "Quality is up 5%."
            assert body["data"]["sources"] == ["performance"]
            assert body["data"]["confidence"] == pytest.approx(0.8)
            # Without these the controller could silently drop
            # ``proposal_id`` / ``alert_id`` or swap the ``ask()`` args
            # and the payload-only checks above would still pass.
            signals_mock.get_org_snapshot.assert_awaited_once()
            # ``since`` is a required keyword-only arg on the service, so
            # the assertion must pin the trailing-window shape without
            # pinning wall time.
            since = signals_mock.get_org_snapshot.await_args.kwargs["since"]
            window = app_state.clock.now() - since
            assert timedelta(days=6) < window < timedelta(days=8)
            chat_mock.ask.assert_awaited_once()
            asked_query, asked_snapshot = chat_mock.ask.await_args.args
            assert isinstance(asked_query, ChatQuery)
            assert asked_query.question == "How is quality trending?"
            assert str(asked_query.proposal_id) == proposal_id
            assert str(asked_query.alert_id) == alert_id
            assert asked_snapshot is expected_snapshot
        finally:
            app_state.swap_slice(original_slice)

    async def test_payload_serialises_cited_records(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """The response flattens ``cited_records`` into typed JSON objects."""
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="Working on the platform revamp.",
            sources=("tasks", "projects"),
            cited_records=(
                CitedRecord(
                    kind="task",
                    record_id=sid("task-1"),
                    label="Fix login",
                    status="in_progress",
                ),
                CitedRecord(
                    kind="project",
                    record_id=sid("proj-1"),
                    label="Platform Revamp",
                    status="active",
                ),
            ),
            confidence=0.9,
        )
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()
        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
        )
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"question": "What is the org working on?"},
            )
            assert resp.status_code == 200
            cited = resp.json()["data"]["cited_records"]
            assert cited == [
                {
                    "kind": "task",
                    "record_id": sid("task-1"),
                    "label": "Fix login",
                    "status": "in_progress",
                },
                {
                    "kind": "project",
                    "record_id": sid("proj-1"),
                    "label": "Platform Revamp",
                    "status": "active",
                },
            ]
        finally:
            app_state.swap_slice(original_slice)

    async def test_returns_503_when_explain_chat_disabled_at_runtime(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """A wired chat backend still 503s when the live flag is toggled off.

        The capability is built at boot (on by default) but live-gated, so
        flipping ``chief_of_staff.explain_chat_enabled`` to false in settings
        takes effect on the next request with no restart.
        """
        from synthorg.settings.state import settings_service_of

        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()
        app_state = async_test_client.app.state.app_state
        settings = settings_service_of(app_state)
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
        )
        # Capture the prior state so cleanup restores it exactly (the
        # settings service is shared across tests on this worker).
        prior = await settings.get("chief_of_staff", "explain_chat_enabled")
        try:
            await settings.set("chief_of_staff", "explain_chat_enabled", "false")
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"question": "How are we doing?"},
            )
            assert resp.status_code == 503
            # The gate fires before dispatch: the backend is never called.
            chat_mock.ask.assert_not_awaited()
        finally:
            if prior.source is SettingSource.DATABASE:
                await settings.set(
                    "chief_of_staff", "explain_chat_enabled", prior.value
                )
            else:
                await settings.delete("chief_of_staff", "explain_chat_enabled")
            app_state.swap_slice(original_slice)

    async def test_snapshot_window_is_live_configurable(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """chief_of_staff.chat_snapshot_window_days applies with no restart."""
        from synthorg.settings.state import settings_service_of

        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="ok", sources=(), confidence=0.5
        )
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()
        app_state = async_test_client.app.state.app_state
        settings = settings_service_of(app_state)
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
        )
        prior = await settings.get("chief_of_staff", "chat_snapshot_window_days")
        try:
            await settings.set("chief_of_staff", "chat_snapshot_window_days", "3")
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"question": "How are we doing?"},
            )
            assert resp.status_code == 200
            since = signals_mock.get_org_snapshot.await_args.kwargs["since"]
            window = app_state.clock.now() - since
            assert timedelta(days=2) < window < timedelta(days=4)
        finally:
            if prior.source is SettingSource.DATABASE:
                await settings.set(
                    "chief_of_staff", "chat_snapshot_window_days", prior.value
                )
            else:
                await settings.delete("chief_of_staff", "chat_snapshot_window_days")
            app_state.swap_slice(original_slice)

    async def test_returns_503_when_signals_service_missing(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """A wired chat backend still 503s if SignalsService is unavailable."""
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=None,
        )
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"question": "How are we doing?"},
            )
            assert resp.status_code == 503
            body = resp.json()
            assert body["success"] is False
            # The 503 surfaces the real reason (which dependency is missing)
            # rather than a bare "Service unavailable" placeholder.
            assert body["error"].startswith("ServiceUnavailableError:")
        finally:
            app_state.swap_slice(original_slice)
