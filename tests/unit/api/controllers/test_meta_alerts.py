"""Tests for the ``GET /meta/alerts`` read endpoint.

Reads durable :class:`Alert` rows through the soft ``alert_repo_of``
accessor: a wired repo serves real alerts (with optional severity /
alert_type filters); an unwired repo degrades to an empty page rather
than 503-ing.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import Alert
from synthorg.meta.models import RuleSeverity
from synthorg.meta.state import MetaStateSlice
from synthorg.persistence.alert_protocol import AlertFilterSpec
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_BASE = "/api/v1/meta/alerts"
_HEADERS = make_auth_headers("ceo")
_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _alert(**overrides: object) -> Alert:
    base: dict[str, object] = {
        "severity": RuleSeverity.WARNING,
        "alert_type": "inflection",
        "description": NotBlankStr("Quality dropped sharply"),
        "affected_domains": (NotBlankStr("performance"),),
        "emitted_at": _NOW,
    }
    base.update(overrides)
    return Alert(**base)  # type: ignore[arg-type]


class _FakeAlertRepo:
    def __init__(self, alerts: tuple[Alert, ...]) -> None:
        self._alerts = alerts

    async def append(self, event: Alert, /) -> None:
        self._alerts = (*self._alerts, event)

    async def query(
        self,
        filter_spec: AlertFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Alert, ...]:
        matches = [
            a
            for a in self._alerts
            if (filter_spec.severity is None or a.severity == filter_spec.severity)
            and (
                filter_spec.alert_type is None or a.alert_type == filter_spec.alert_type
            )
        ]
        return tuple(matches[offset : offset + limit])

    async def purge_before(self, threshold: datetime, /) -> int:
        return 0

    async def get_by_id(self, alert_id: UUID, /) -> Alert | None:
        return next((a for a in self._alerts if a.id == alert_id), None)


class TestListAlerts:
    """``GET /meta/alerts`` pages durable alerts, empty when unwired."""

    async def test_empty_page_when_repo_absent(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, alert_repo=None)
        try:
            resp = await async_test_client.get(_BASE, headers=_HEADERS)
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"] == []
        finally:
            app_state.swap_slice(original)

    async def test_lists_wired_alerts(self, async_test_client: LoopAsyncClient) -> None:
        alert = _alert()
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, alert_repo=_FakeAlertRepo((alert,)))
        try:
            resp = await async_test_client.get(_BASE, headers=_HEADERS)
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["data"]) == 1
            assert body["data"][0]["id"] == str(alert.id)
            assert body["data"][0]["severity"] == "warning"
            assert body["data"][0]["alert_type"] == "inflection"
            assert body["data"][0]["affected_domains"] == ["performance"]
        finally:
            app_state.swap_slice(original)

    async def test_filters_by_severity(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        info_alert = _alert(severity=RuleSeverity.INFO)
        critical_alert = _alert(severity=RuleSeverity.CRITICAL)
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice, alert_repo=_FakeAlertRepo((info_alert, critical_alert))
        )
        try:
            resp = await async_test_client.get(
                _BASE, headers=_HEADERS, params={"severity": "critical"}
            )
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["data"]) == 1
            assert body["data"][0]["severity"] == "critical"
        finally:
            app_state.swap_slice(original)

    async def test_unresolvable_alert_id_is_absent_from_repo(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, alert_repo=_FakeAlertRepo(()))
        try:
            repo = app_state.slice(MetaStateSlice).alert_repo
            assert repo is not None
            assert await repo.get_by_id(uuid4()) is None
        finally:
            app_state.swap_slice(original)
