"""Unit tests for the analytics + reports MCP handlers."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.api.state import AppState
from synthorg.core.types import NotBlankStr
from synthorg.meta.analytics.models import (
    AnalyticsForecast,
    AnalyticsOverview,
    AnalyticsTrends,
    MetricsHistory,
    MetricsSnapshot,
)
from synthorg.meta.mcp.handlers.analytics import ANALYTICS_HANDLERS
from synthorg.meta.reports.models import Report, ReportStatus
from synthorg.meta.state import MetaStateSlice
from tests._shared import make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


def _overview() -> AnalyticsOverview:
    return AnalyticsOverview(
        avg_quality_score=8.0,
        avg_success_rate=0.9,
        total_spend=100.0,
        total_error_findings=0,
        total_proposals=0,
    )


@pytest.fixture
def fake_analytics() -> AsyncMock:
    service = AsyncMock()
    service.get_overview = AsyncMock(return_value=_overview())
    service.get_trends = AsyncMock(
        return_value=AnalyticsTrends(metrics=(), window_days=7),
    )
    service.get_forecast = AsyncMock(
        return_value=AnalyticsForecast(
            horizon_days=30,
            confidence=0.5,
            projected_spend=50.0,
        ),
    )
    service.get_current_metrics = AsyncMock(
        return_value=MetricsSnapshot(metrics={"budget.total_spend": 100.0}),
    )
    service.get_metric_history = AsyncMock(
        return_value=MetricsHistory(metric_names=(), points=()),
    )
    return service


@pytest.fixture
def fake_reports() -> AsyncMock:
    service = AsyncMock()
    service.list_reports = AsyncMock(return_value=((), 0))
    service.get_report = AsyncMock(return_value=None)
    service.generate_report = AsyncMock(
        return_value=Report(
            template=NotBlankStr("org_overview"),
            title=NotBlankStr("Overview"),
            status=ReportStatus.READY,
            author_id=NotBlankStr("a"),
        ),
    )
    return service


@pytest.fixture
def fake_app_state(
    fake_analytics: AsyncMock,
    fake_reports: AsyncMock,
) -> AppState:
    return make_app_state(
        slices={
            MetaStateSlice: {
                "analytics_service": fake_analytics,
                "reports_service": fake_reports,
            },
        },
    )


def _iso(offset_minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=offset_minutes)).isoformat()


class TestAnalyticsOverview:
    async def test_happy_path(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_analytics_get_overview"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"since": _iso(-60)},
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert payload["data"]["total_spend"] == 100.0

    async def test_missing_since_rejected(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_analytics_get_overview"]
        response = await handler(
            app_state=fake_app_state,
            arguments={},
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "invalid_argument"


class TestAnalyticsTrends:
    async def test_until_required(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_analytics_get_trends"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"since": _iso(-60)},
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "invalid_argument"

    async def test_happy_path(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_analytics_get_trends"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"since": _iso(-60), "until": _iso()},
        )
        assert json.loads(response)["status"] == "ok"


class TestMetricsHandlers:
    async def test_current_ok(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_metrics_get_current"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"since": _iso(-60)},
        )
        assert json.loads(response)["status"] == "ok"

    async def test_history_requires_metric_names(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_metrics_get_history"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"since": _iso(-60), "until": _iso()},
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "invalid_argument"

    async def test_history_ok(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_metrics_get_history"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "since": _iso(-60),
                "until": _iso(),
                "metric_names": ["budget.total_spend"],
                "sample_count": 4,
            },
        )
        assert json.loads(response)["status"] == "ok"


class TestReportsHandlers:
    async def test_list(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_reports_list"]
        response = await handler(
            app_state=fake_app_state,
            arguments={},
        )
        assert json.loads(response)["status"] == "ok"

    async def test_get_not_found(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_reports_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"report_id": str(uuid4())},
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "not_found"

    async def test_get_invalid_uuid(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_reports_get"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"report_id": "not-a-uuid"},
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "invalid_argument"

    async def test_generate_happy_path(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_reports_generate"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"template": "org_overview"},
            actor=make_test_actor(name="reporter"),
        )
        assert json.loads(response)["status"] == "ok"

    async def test_generate_rejects_anonymous_actor(
        self,
        fake_app_state: AppState,
        fake_reports: AsyncMock,
    ) -> None:
        # Report generation persists ``author_id`` and must reject
        # anonymous callers rather than silently storing
        # ``"mcp-anonymous"`` (the previous behaviour). With ``actor=None``
        # the handler hits ``require_actor_id`` and the
        # ArgumentValidationError envelope is returned WITHOUT touching
        # the service layer -- attribution validation must short-circuit
        # before any persistence call so the wire failure is "actor
        # required" rather than "report half-created with a synthetic
        # author".
        handler = ANALYTICS_HANDLERS["synthorg_reports_generate"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"template": "org_overview"},
            actor=None,
        )
        body = json.loads(response)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"
        # Service-side regression guard: the validation failure must
        # happen BEFORE we hit the reports service, so the mocked
        # generate_report call is never made.
        fake_reports.generate_report.assert_not_called()

    async def test_generate_requires_template(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = ANALYTICS_HANDLERS["synthorg_reports_generate"]
        response = await handler(
            app_state=fake_app_state,
            arguments={},
            actor=make_test_actor(name="reporter"),
        )
        assert json.loads(response)["status"] == "error"
