"""Clock-seam wiring for the integration health checks.

Each check measures request latency with the injected ``Clock`` seam
so the elapsed value is deterministic under ``FakeClock`` and immune
to a wall-clock change mid-request. These tests assert the seam is
threaded through every check constructor (the production registry
constructs them with no clock, defaulting to ``SystemClock``).
"""

import pytest

from synthorg.integrations.health.checks.generic_http import (
    GenericHttpHealthCheck,
)
from synthorg.integrations.health.checks.github import GitHubHealthCheck
from synthorg.integrations.health.checks.slack import SlackHealthCheck
from synthorg.integrations.health.checks.smtp import SmtpHealthCheck
from tests._shared import FakeClock


@pytest.mark.unit
class TestHealthCheckClockSeam:
    """Every health check threads the injected Clock seam."""

    def test_smtp_uses_injected_clock(self) -> None:
        fake = FakeClock()
        assert SmtpHealthCheck(clock=fake)._clock is fake

    def test_slack_uses_injected_clock(self) -> None:
        fake = FakeClock()
        assert SlackHealthCheck(clock=fake)._clock is fake

    def test_github_uses_injected_clock(self) -> None:
        fake = FakeClock()
        assert GitHubHealthCheck(clock=fake)._clock is fake

    def test_generic_http_uses_injected_clock(self) -> None:
        fake = FakeClock()
        assert GenericHttpHealthCheck(clock=fake)._clock is fake

    def test_default_construction_uses_system_clock(self) -> None:
        # The prober registry constructs with no clock; the seam must
        # still yield a working SystemClock so production is unaffected.
        from synthorg.core.clock import SystemClock

        assert isinstance(SmtpHealthCheck()._clock, SystemClock)
