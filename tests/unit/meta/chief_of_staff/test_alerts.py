"""Unit tests for ProactiveAlertService, LoggingAlertSink, and
PersistentAlertSink."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.meta.chief_of_staff.alerts import (
    LoggingAlertSink,
    PersistentAlertSink,
    ProactiveAlertService,
)
from synthorg.meta.chief_of_staff.models import Alert, OrgInflection
from synthorg.meta.models import RuleSeverity
from synthorg.persistence.alert_protocol import AlertRepository
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)


def _make_inflection(
    *,
    severity: RuleSeverity = RuleSeverity.WARNING,
) -> OrgInflection:
    return OrgInflection(
        severity=severity,
        affected_domains=("performance",),
        metric_name="quality_score",
        old_value=7.5,
        new_value=5.0,
        description="Quality dropped 33%",
        detected_at=_NOW,
    )


class _CollectingSink:
    """Test sink that collects alerts for assertion."""

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    async def on_alert(self, alert: Alert) -> None:
        self.alerts.append(alert)


class TestProactiveAlertService:
    """ProactiveAlertService tests."""

    async def test_emits_when_above_threshold(self) -> None:
        sink = _CollectingSink()
        service = ProactiveAlertService(
            alert_sinks=(sink,),
            severity_threshold=RuleSeverity.WARNING,
        )
        inflection = _make_inflection(severity=RuleSeverity.WARNING)
        await service.on_inflection(inflection)
        assert len(sink.alerts) == 1
        assert sink.alerts[0].severity is RuleSeverity.WARNING
        assert sink.alerts[0].alert_type == "inflection"

    async def test_emits_critical_above_warning_threshold(self) -> None:
        sink = _CollectingSink()
        service = ProactiveAlertService(
            alert_sinks=(sink,),
            severity_threshold=RuleSeverity.WARNING,
        )
        inflection = _make_inflection(severity=RuleSeverity.CRITICAL)
        await service.on_inflection(inflection)
        assert len(sink.alerts) == 1
        assert sink.alerts[0].severity is RuleSeverity.CRITICAL

    async def test_suppresses_below_threshold(self) -> None:
        sink = _CollectingSink()
        service = ProactiveAlertService(
            alert_sinks=(sink,),
            severity_threshold=RuleSeverity.WARNING,
        )
        inflection = _make_inflection(severity=RuleSeverity.INFO)
        await service.on_inflection(inflection)
        assert len(sink.alerts) == 0

    async def test_multiple_sinks(self) -> None:
        sink1 = _CollectingSink()
        sink2 = _CollectingSink()
        service = ProactiveAlertService(
            alert_sinks=(sink1, sink2),
            severity_threshold=RuleSeverity.INFO,
        )
        await service.on_inflection(_make_inflection())
        assert len(sink1.alerts) == 1
        assert len(sink2.alerts) == 1

    async def test_alert_contains_signal_context(self) -> None:
        sink = _CollectingSink()
        service = ProactiveAlertService(
            alert_sinks=(sink,),
            severity_threshold=RuleSeverity.INFO,
        )
        await service.on_inflection(_make_inflection())
        alert = sink.alerts[0]
        assert "metric" in alert.signal_context
        assert alert.signal_context["old_value"] == pytest.approx(7.5)
        assert alert.signal_context["new_value"] == pytest.approx(5.0)

    async def test_no_sinks_no_error(self) -> None:
        service = ProactiveAlertService(
            alert_sinks=(),
            severity_threshold=RuleSeverity.INFO,
        )
        await service.on_inflection(_make_inflection())


class TestLoggingAlertSink:
    """LoggingAlertSink tests."""

    async def test_handles_warning_alert(self) -> None:
        sink = LoggingAlertSink()
        alert = Alert(
            severity=RuleSeverity.WARNING,
            alert_type="inflection",
            description="Test alert",
            affected_domains=("budget",),
            emitted_at=_NOW,
        )
        await sink.on_alert(alert)

    async def test_handles_critical_alert(self) -> None:
        sink = LoggingAlertSink()
        alert = Alert(
            severity=RuleSeverity.CRITICAL,
            alert_type="threshold",
            description="Critical test",
            affected_domains=("performance",),
            emitted_at=_NOW,
        )
        await sink.on_alert(alert)


class TestPersistentAlertSink:
    """PersistentAlertSink tests."""

    async def test_appends_alert_to_repo(self) -> None:
        repo = mock_of[AlertRepository](append=AsyncMock(return_value=None))
        sink = PersistentAlertSink(repo)
        alert = Alert(
            severity=RuleSeverity.WARNING,
            alert_type="inflection",
            description="Test alert",
            affected_domains=("budget",),
            emitted_at=_NOW,
        )
        await sink.on_alert(alert)
        repo.append.assert_awaited_once_with(alert)

    async def test_swallows_append_failure(self) -> None:
        repo = mock_of[AlertRepository](
            append=AsyncMock(side_effect=RuntimeError("db down"))
        )
        sink = PersistentAlertSink(repo)
        alert = Alert(
            severity=RuleSeverity.WARNING,
            alert_type="inflection",
            description="Test alert",
            affected_domains=("budget",),
            emitted_at=_NOW,
        )
        # Must not raise: ProactiveAlertService's fan-out already isolates
        # sink failures, so a persistence outage cannot take the tick down.
        await sink.on_alert(alert)
