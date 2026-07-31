"""The health probe must never buy anything to colour a badge green.

Every vendor here fronts a metered API: the endpoint the connection points at
is the product, and a request that succeeds is a request that is billed. The
probe therefore sends no query at all and reads the rejection, which the vendor
does not charge for.

The regression these lock in cost real money. Each vendor preset used to carry
a "smallest well-formed search" so the endpoint would answer 200 and the badge
would read healthy; on the background loop that spent one search per connection
per cycle, indefinitely, without anyone asking for a search.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.core.clock import Clock
from synthorg.integrations.config import IntegrationHealthConfig
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.http_vendor import (
    HTTP_VENDOR_PRESETS,
    HttpVendor,
    ProbeVerdict,
)
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionHealth,
    ConnectionStatus,
    ConnectionType,
)
from synthorg.integrations.health.prober import HealthProberService
from tests._shared import FakeClock, as_uuid, mock_of

_NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _connection(
    name: str,
    *,
    status: ConnectionStatus,
    last_check_at: datetime | None,
) -> Connection:
    return Connection(
        id=as_uuid(name),
        name=name,
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        base_url="https://api.example.test/search",
        health_check_enabled=True,
        health=ConnectionHealth(status=status, last_check_at=last_check_at),
    )


def _prober(clock: Clock) -> HealthProberService:
    return HealthProberService(
        catalog=mock_of[ConnectionCatalog](),
        healthy_recheck_seconds=21_600,
        degraded_recheck_seconds=1_800,
        unhealthy_recheck_seconds=300,
        clock=clock,
    )


@pytest.mark.unit
class TestNoVendorProbeIsBilled:
    """No shipped preset may send a payload its vendor would charge for."""

    def test_no_preset_declares_a_consuming_payload(self) -> None:
        fields = set(
            type(HTTP_VENDOR_PRESETS[HttpVendor.BRAVE]).model_fields,
        )

        assert "health_params" not in fields
        assert "health_body" not in fields

    def test_brave_proves_a_good_key_from_a_rejection(self) -> None:
        # Brave documents that only non-error responses are billed, so the
        # 422 for a missing `q` is free. Observed against the live API: a
        # valid token is rejected for the QUERY, an invalid one for the TOKEN.
        brave = HTTP_VENDOR_PRESETS[HttpVendor.BRAVE]
        valid_key_body = (
            '{"error":{"code":"VALIDATION","meta":{"errors":['
            '{"type":"missing","loc":["query","q"],"msg":"Field required"}]}}}'
        )

        assert brave.probe_verdict(422, valid_key_body) is ProbeVerdict.AUTH_OK

    def test_brave_still_catches_a_revoked_key(self) -> None:
        # The saving is worthless if it also stops reporting a dead credential.
        brave = HTTP_VENDOR_PRESETS[HttpVendor.BRAVE]
        body = (
            '{"error":{"code":"SUBSCRIPTION_TOKEN_INVALID","detail":'
            '"The provided subscription token is invalid.","status":422}}'
        )

        assert brave.probe_verdict(422, body) is ProbeVerdict.AUTH_FAILED

    def test_an_unverified_vendor_claims_nothing(self) -> None:
        # Exa publishes no free way to check a key, so its preset asserts no
        # error contract and the probe must not invent one in either
        # direction.
        exa = HTTP_VENDOR_PRESETS[HttpVendor.EXA]

        assert exa.probe_verdict(422, "{}") is ProbeVerdict.INDETERMINATE
        assert exa.auth_cleared_statuses == frozenset()


@pytest.mark.unit
class TestProbeIsDueOnlyWhenItsVerdictExpired:
    """A verdict is trusted for a window sized by what it said."""

    def test_a_healthy_connection_is_not_reprobed_for_hours(self) -> None:
        # The regression: the loop probed every connection on every tick, so a
        # metered API was charged twelve times an hour to re-confirm a
        # credential that had not changed.
        prober = _prober(FakeClock(start=_NOW))
        conn = _connection(
            "brave",
            status=ConnectionStatus.HEALTHY,
            last_check_at=_NOW - timedelta(minutes=5),
        )

        assert prober._is_due(conn) is False

    def test_a_healthy_connection_is_due_once_its_window_passes(self) -> None:
        prober = _prober(FakeClock(start=_NOW))
        conn = _connection(
            "brave",
            status=ConnectionStatus.HEALTHY,
            last_check_at=_NOW - timedelta(hours=7),
        )

        assert prober._is_due(conn) is True

    def test_a_failing_connection_is_retried_quickly(self) -> None:
        # The operator is watching this one recover, so it keeps the short
        # cadence the healthy case gives up.
        prober = _prober(FakeClock(start=_NOW))
        conn = _connection(
            "broken",
            status=ConnectionStatus.UNHEALTHY,
            last_check_at=_NOW - timedelta(minutes=6),
        )

        assert prober._is_due(conn) is True

    def test_a_degraded_connection_sits_between_the_two(self) -> None:
        prober = _prober(FakeClock(start=_NOW))
        recent = _connection(
            "flaky",
            status=ConnectionStatus.DEGRADED,
            last_check_at=_NOW - timedelta(minutes=6),
        )
        stale = _connection(
            "flaky",
            status=ConnectionStatus.DEGRADED,
            last_check_at=_NOW - timedelta(minutes=45),
        )

        assert prober._is_due(recent) is False
        assert prober._is_due(stale) is True

    def test_a_never_checked_connection_is_due_immediately(self) -> None:
        prober = _prober(FakeClock(start=_NOW))
        conn = _connection(
            "fresh",
            status=ConnectionStatus.UNKNOWN,
            last_check_at=None,
        )

        assert prober._is_due(conn) is True


@pytest.mark.unit
class TestRecheckCadenceRunsOneWay:
    """The intervals only control cost while they shorten as health worsens."""

    def test_an_inverted_cadence_is_refused(self) -> None:
        # Each interval is separately mirrored and separately bounded, so
        # without this nothing stops a configuration that re-probes working
        # connections more often than failing ones.
        with pytest.raises(ValidationError, match="must shorten as health worsens"):
            IntegrationHealthConfig(
                healthy_recheck_seconds=60,
                degraded_recheck_seconds=1_800,
                unhealthy_recheck_seconds=300,
            )

    def test_the_defaults_satisfy_the_ordering(self) -> None:
        config = IntegrationHealthConfig()

        assert (
            config.unhealthy_recheck_seconds
            <= config.degraded_recheck_seconds
            <= config.healthy_recheck_seconds
        )

    def test_equal_intervals_are_accepted(self) -> None:
        # A deployment that wants one cadence for everything is expressing a
        # preference, not an inversion.
        config = IntegrationHealthConfig(
            healthy_recheck_seconds=300,
            degraded_recheck_seconds=300,
            unhealthy_recheck_seconds=300,
        )

        assert config.healthy_recheck_seconds == 300
