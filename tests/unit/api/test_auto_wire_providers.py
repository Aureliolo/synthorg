"""The Connections screen and the Providers screen must not disagree.

Both read the same tracker, so the only way they can differ is by asking it
about different moments. The tracker measures its 24-hour window back from the
reference time it is handed, so a lookup on wall time against outcomes recorded
on an injected clock excludes every one of them and answers with a verdict
about nothing.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest

from synthorg.api.auto_wire_providers import (
    bind_connection_health_to_tracker,
    wire_provider_registry,
)
from synthorg.config.schema import (
    ProviderConfig,
    ProviderModelConfig,
    RootConfig,
)
from synthorg.integrations.health.checks.llm_provider import ProviderHealthLookup
from synthorg.providers.enums import AuthType
from synthorg.providers.health import ProviderHealthRecord, ProviderHealthStatus
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.providers.registry import ProviderRegistry
from tests._shared import FakeClock

_BIND_TARGET = "synthorg.integrations.health.prober.bind_provider_health_lookup"


def _bound_lookup(
    tracker: ProviderHealthTracker, clock: FakeClock
) -> ProviderHealthLookup:
    """Bind the lookup and hand back the closure that was installed.

    Captured rather than reached through the check registry, which is a
    module-level singleton a test must not leave rebound for its neighbours.

    Returns:
        The lookup ``bind_connection_health_to_tracker`` installed.
    """
    captured: list[ProviderHealthLookup] = []
    with patch(_BIND_TARGET, captured.append):
        bind_connection_health_to_tracker(tracker, clock=clock)
    return captured[0]


def _config(*, providers: dict[str, ProviderConfig] | None = None) -> RootConfig:
    """Build a root config carrying *providers*.

    Returns:
        The config, with one example provider when none is given.
    """
    return RootConfig(
        company_name="test",
        providers=providers
        if providers is not None
        else {
            "test-provider": ProviderConfig(
                driver="litellm",
                auth_type=AuthType.NONE,
                base_url="http://localhost:11434",
                models=(ProviderModelConfig(id="example-basic-001"),),
            )
        },
    )


@pytest.mark.unit
class TestConnectionHealthLookup:
    async def test_a_provider_connection_reports_the_tracked_verdict(self) -> None:
        """And on the clock the outcomes were recorded with.

        ``calls_last_24h`` is the discriminating assertion: the record is
        stamped at the injected clock's time, which is months from wall time,
        so a lookup reading on wall time returns the empty summary and this
        reads zero.
        """
        clock = FakeClock()
        tracker = ProviderHealthTracker()
        await tracker.record(
            ProviderHealthRecord(
                provider_name="alpha",
                timestamp=clock.now() - timedelta(minutes=1),
                success=False,
                response_time_ms=120.0,
                error_message="refused",
            )
        )

        summary = await _bound_lookup(tracker, clock)("provider-alpha")

        assert summary is not None
        assert summary.calls_last_24h == 1
        assert summary.health_status is ProviderHealthStatus.DOWN

    async def test_a_connection_outside_the_convention_is_not_a_provider(self) -> None:
        """It keeps the reachability probe rather than borrowing a verdict."""
        lookup = _bound_lookup(ProviderHealthTracker(), FakeClock())

        assert await lookup("chat-main") is None

    async def test_an_untracked_provider_reports_unknown(self) -> None:
        lookup = _bound_lookup(ProviderHealthTracker(), FakeClock())

        summary = await lookup("provider-alpha")

        assert summary is not None
        assert summary.health_status is ProviderHealthStatus.UNKNOWN


@pytest.mark.unit
class TestWireProviderRegistry:
    def test_builds_the_configured_providers(self) -> None:
        registry = wire_provider_registry(_config())

        assert "test-provider" in registry

    def test_a_build_failure_surfaces(self) -> None:
        """Boot must not continue with a registry that could not be built.

        Swallowing it would leave every dispatch resolving to nothing, which
        the caller reads as a provider-less install rather than a fault.
        """
        with (
            patch.object(
                ProviderRegistry,
                "from_config",
                side_effect=RuntimeError("driver blew up"),
            ),
            pytest.raises(RuntimeError, match="driver blew up"),
        ):
            _ = wire_provider_registry(_config())
