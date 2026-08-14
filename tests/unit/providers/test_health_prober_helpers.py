"""The prober's settings reads, which must survive the settings backend.

Both helpers are read once per probe cycle, so a settings-backend outage hits
them on every cycle for as long as it lasts. Failing closed would silently
stop provider observability exactly when something is already wrong, and
reporting per cycle would tile the operator's log with the same line at the
probe cadence. They therefore fail safe and report once per outage, which is
two behaviours a caller cannot see from the returned value alone.
"""

from typing import cast
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from synthorg.observability.events.provider import (
    PROVIDER_HEALTH_PROBER_INTERVAL_FALLBACK,
    PROVIDER_HEALTH_PROBER_RESOLVE_FAILED,
    PROVIDER_HEALTH_PROBER_RESOLVE_RECOVERED,
)
from synthorg.providers.health_prober_helpers import (
    build_ping_url,
    resolve_probe_interval,
    resolve_prober_enabled,
    truncate,
)
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

_FALLBACK_SECONDS = 300


def _resolver(*, bool_value: bool = True, int_value: int = 60) -> ConfigResolver:
    """A resolver double answering both reads.

    Returns:
        The double, answering ``get_bool`` and ``get_int`` with the given
        values.
    """
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool = AsyncMock(return_value=bool_value)
    resolver.get_int = AsyncMock(return_value=int_value)
    return cast(ConfigResolver, resolver)


def _failing_resolver() -> ConfigResolver:
    """A resolver double standing in for a degraded settings backend.

    Returns:
        The double, raising from both reads.
    """
    resolver = mock_of[ConfigResolver]()
    resolver.get_bool = AsyncMock(side_effect=RuntimeError("settings down"))
    resolver.get_int = AsyncMock(side_effect=RuntimeError("settings down"))
    return cast(ConfigResolver, resolver)


@pytest.mark.unit
class TestResolveProberEnabled:
    async def test_reports_what_the_operator_set(self) -> None:
        assert await resolve_prober_enabled(_resolver(bool_value=False)) == (
            False,
            False,
        )

    async def test_an_outage_leaves_probing_on(self) -> None:
        """Fail safe, not closed.

        An operator pauses probing by setting the flag. A settings backend
        that cannot be read has not been asked to pause anything, and
        stopping there would remove the observability that says something is
        wrong at the moment something is.
        """
        enabled, reported = await resolve_prober_enabled(_failing_resolver())
        assert enabled is True
        assert reported is True

    async def test_the_first_failure_of_an_outage_is_reported(self) -> None:
        with capture_logs() as logs:
            _ = await resolve_prober_enabled(_failing_resolver())

        assert [
            entry
            for entry in logs
            if entry["event"] == PROVIDER_HEALTH_PROBER_RESOLVE_FAILED
        ]

    async def test_a_continuing_outage_stays_quiet(self) -> None:
        """Otherwise one degraded backend writes a line per probe cycle."""
        with capture_logs() as logs:
            _ = await resolve_prober_enabled(_failing_resolver(), already_reported=True)

        assert not [
            entry
            for entry in logs
            if entry["event"] == PROVIDER_HEALTH_PROBER_RESOLVE_FAILED
        ]

    async def test_recovery_is_reported_so_the_outage_has_an_end(self) -> None:
        with capture_logs() as logs:
            enabled, reported = await resolve_prober_enabled(
                _resolver(), already_reported=True
            )

        assert (enabled, reported) == (True, False)
        assert [
            entry
            for entry in logs
            if entry["event"] == PROVIDER_HEALTH_PROBER_RESOLVE_RECOVERED
        ]


@pytest.mark.unit
class TestResolveProbeInterval:
    async def test_reports_the_cadence_the_operator_set(self) -> None:
        assert await resolve_probe_interval(
            _resolver(int_value=45), fallback=_FALLBACK_SECONDS
        ) == (45, False)

    async def test_an_outage_keeps_the_current_cadence(self) -> None:
        interval, reported = await resolve_probe_interval(
            _failing_resolver(), fallback=_FALLBACK_SECONDS
        )
        assert interval == _FALLBACK_SECONDS
        assert reported is True

    async def test_a_sub_second_cadence_is_refused(self) -> None:
        """Zero would spin the loop rather than slow it down."""
        assert await resolve_probe_interval(
            _resolver(int_value=0), fallback=_FALLBACK_SECONDS
        ) == (_FALLBACK_SECONDS, False)

    async def test_the_first_failure_of_an_outage_is_reported(self) -> None:
        with capture_logs() as logs:
            _ = await resolve_probe_interval(
                _failing_resolver(), fallback=_FALLBACK_SECONDS
            )

        assert [
            entry
            for entry in logs
            if entry["event"] == PROVIDER_HEALTH_PROBER_INTERVAL_FALLBACK
        ]

    async def test_a_continuing_outage_stays_quiet(self) -> None:
        with capture_logs() as logs:
            _ = await resolve_probe_interval(
                _failing_resolver(),
                fallback=_FALLBACK_SECONDS,
                already_reported=True,
            )

        assert not [
            entry
            for entry in logs
            if entry["event"] == PROVIDER_HEALTH_PROBER_INTERVAL_FALLBACK
        ]


@pytest.mark.unit
class TestPingUrlPortBounds:
    @pytest.mark.parametrize("port", [0, 65536, -1])
    def test_a_port_outside_the_tcp_range_is_refused(self, port: int) -> None:
        """The resolver validates at write time; this is the last stop.

        A caller that reached here with a nonsense port would otherwise build
        a URL nothing answers and record the silence as a provider fault.
        """
        with pytest.raises(ValueError, match="ollama_port"):
            _ = build_ping_url("http://host:11434", None, ollama_port=port)


@pytest.mark.unit
class TestTruncate:
    def test_a_short_message_is_untouched(self) -> None:
        assert truncate("short", 10) == "short"

    def test_a_long_message_keeps_the_length_contract(self) -> None:
        assert truncate("abcdefghij", 5) == "ab..."

    def test_a_limit_too_small_for_the_ellipsis_still_fits(self) -> None:
        """The suffix cannot be added without breaking the cap it enforces."""
        assert truncate("abcdefghij", 2) == "ab"
