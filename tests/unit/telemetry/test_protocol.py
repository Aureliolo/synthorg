"""Tests for telemetry protocol and event model."""

import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.telemetry.protocol import TelemetryEvent, TelemetryReporter
from synthorg.telemetry.reporters.noop import NoopReporter


@pytest.mark.unit
class TestTelemetryEvent:
    """TelemetryEvent model validation."""

    def test_minimal_event(self) -> None:
        event = TelemetryEvent(
            event_type="deployment.heartbeat",
            deployment_id="abc-123",
            synthorg_version="0.6.4",
            python_version="3.14.0",
            os_platform="Linux",
            timestamp=datetime.now(UTC),
        )
        assert event.event_type == "deployment.heartbeat"
        assert event.properties == {}

    def test_event_with_properties(self) -> None:
        # ``graceful`` (bool) is a shutdown-only key; the
        # construction-time property guard now rejects it on a
        # heartbeat. Lock that contract before exercising the valid
        # path, then use a shutdown event so the int/float/str/bool
        # primitive coverage stays within that type's allowlist.
        with pytest.raises(ValidationError):
            TelemetryEvent(
                event_type="deployment.heartbeat",
                deployment_id="abc-123",
                synthorg_version="0.6.4",
                python_version="3.14.0",
                os_platform="Linux",
                timestamp=datetime.now(UTC),
                properties={"uptime_hours": 1.0, "graceful": True},
            )

        event = TelemetryEvent(
            event_type="deployment.heartbeat",
            deployment_id="abc-123",
            synthorg_version="0.6.4",
            python_version="3.14.0",
            os_platform="Linux",
            timestamp=datetime.now(UTC),
            properties={
                "agent_count": 5,
                "uptime_hours": 12.5,
                "template_name": "startup",
            },
        )
        assert event.properties["agent_count"] == 5
        assert event.properties["uptime_hours"] == 12.5
        assert event.properties["template_name"] == "startup"

        shutdown = TelemetryEvent(
            event_type="deployment.shutdown",
            deployment_id="abc-123",
            synthorg_version="0.6.4",
            python_version="3.14.0",
            os_platform="Linux",
            timestamp=datetime.now(UTC),
            properties={"uptime_hours": 1.0, "graceful": True},
        )
        assert shutdown.properties["graceful"] is True

    def test_frozen(self) -> None:
        event = TelemetryEvent(
            event_type="deployment.heartbeat",
            deployment_id="abc-123",
            synthorg_version="0.6.4",
            python_version="3.14.0",
            os_platform="Linux",
            timestamp=datetime.now(UTC),
        )
        with pytest.raises(Exception, match="frozen"):
            event.event_type = "changed"  # type: ignore[misc]

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match="Input should be a finite number"):
            TelemetryEvent(
                event_type="deployment.heartbeat",
                deployment_id="abc-123",
                synthorg_version="0.6.4",
                python_version="3.14.0",
                os_platform="Linux",
                timestamp=datetime.now(UTC),
                properties={"bad": math.nan},
            )

    def test_rejects_inf(self) -> None:
        with pytest.raises(ValueError, match="Input should be a finite number"):
            TelemetryEvent(
                event_type="deployment.heartbeat",
                deployment_id="abc-123",
                synthorg_version="0.6.4",
                python_version="3.14.0",
                os_platform="Linux",
                timestamp=datetime.now(UTC),
                properties={"bad": math.inf},
            )

    def test_environment_defaults_to_dev(self) -> None:
        event = TelemetryEvent(
            event_type="deployment.heartbeat",
            deployment_id="abc-123",
            synthorg_version="0.6.4",
            python_version="3.14.0",
            os_platform="Linux",
            timestamp=datetime.now(UTC),
        )
        assert event.environment == "dev"

    def test_environment_accepts_explicit_value(self) -> None:
        event = TelemetryEvent(
            event_type="deployment.heartbeat",
            deployment_id="abc-123",
            synthorg_version="0.6.4",
            python_version="3.14.0",
            os_platform="Linux",
            environment="prod",
            timestamp=datetime.now(UTC),
        )
        assert event.environment == "prod"

    @pytest.mark.parametrize("environment", ["", "   ", "\t"])
    def test_environment_rejects_blank(self, environment: str) -> None:
        """``NotBlankStr`` rejects empty and whitespace-only values."""
        with pytest.raises(ValidationError):
            TelemetryEvent(
                event_type="deployment.heartbeat",
                deployment_id="abc-123",
                synthorg_version="0.6.4",
                python_version="3.14.0",
                os_platform="Linux",
                environment=environment,
                timestamp=datetime.now(UTC),
            )


@pytest.mark.unit
class TestTelemetryReporterProtocol:
    """TelemetryReporter protocol compliance."""

    def test_noop_is_reporter(self) -> None:
        assert isinstance(NoopReporter(), TelemetryReporter)

    async def test_noop_report_is_silent(self) -> None:
        reporter = NoopReporter()
        event = TelemetryEvent(
            event_type="deployment.heartbeat",
            deployment_id="abc-123",
            synthorg_version="0.6.4",
            python_version="3.14.0",
            os_platform="Linux",
            timestamp=datetime.now(UTC),
        )
        await reporter.report(event)
        await reporter.flush()
        await reporter.shutdown()
