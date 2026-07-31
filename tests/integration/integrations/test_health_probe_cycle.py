"""Health prober cycle: registry, probe loop, and status smoothing.

Exercises the ``HealthProberService`` against a fake catalog with
scripted health check outcomes:

- HEALTHY outcomes reset the failure count.
- Non-HEALTHY outcomes count up to ``degraded_threshold`` and then
  flip to ``DEGRADED``; further failures flip to ``UNHEALTHY``.
- The final status is written back via ``catalog.update_health``.

The test also verifies the check registry is a ``MappingProxyType``
(read-only) with five entries covering GitHub, Slack, SMTP, database,
and generic HTTP.
"""

from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

import pytest
from typeguard import suppress_type_checks

from synthorg.integrations.connections.models import (
    Connection,
    ConnectionHealth,
    ConnectionStatus,
    ConnectionType,
    HealthReport,
    WebhookIngestState,
)
from synthorg.integrations.health import prober as prober_mod
from synthorg.integrations.health.prober import (
    _CHECK_REGISTRY,
    HealthProberService,
    get_health_checker,
)
from tests._shared import FakeClock

# A verdict stays trusted for its recheck interval, so a test driving
# successive cycles has to outlive one. Short enough to read, and the
# advance clears it outright rather than landing on the boundary.
_RECHECK_SECONDS: Final[int] = 60
_PAST_RECHECK_SECONDS: Final[int] = 61


class _FakeCatalog:
    """Minimal catalog stub exposing only what the prober needs."""

    def __init__(self, connections: tuple[Connection, ...]) -> None:
        self._connections = {c.name: c for c in connections}
        self.updates: list[tuple[str, ConnectionStatus]] = []

    async def list_all(self) -> tuple[Connection, ...]:
        return tuple(self._connections.values())

    async def get(self, name: str) -> Connection | None:
        return self._connections.get(name)

    async def update_health(
        self,
        name: str,
        *,
        status: ConnectionStatus,
        checked_at: datetime,
        detail: str | None = None,
        latency_ms: float | None = None,
        webhook_ingest: WebhookIngestState = WebhookIngestState.NOT_APPLICABLE,
        retry_after_seconds: float | None = None,
    ) -> None:
        # Mirrors ConnectionCatalog.update_health in full: the prober records
        # through a broad-except, so a signature that drops a keyword turns
        # every probe into a swallowed TypeError and zero recorded updates.
        self.updates.append((name, status))
        conn = self._connections[name]
        self._connections[name] = conn.model_copy(
            update={
                "health": ConnectionHealth(
                    status=status,
                    last_check_at=checked_at,
                    detail=detail,
                    latency_ms=latency_ms,
                    webhook_ingest=webhook_ingest,
                    retry_after_seconds=retry_after_seconds,
                )
            }
        )


class _ScriptedChecker:
    """Checker that returns a scripted status for the requested connection."""

    def __init__(self, scripted: dict[str, list[ConnectionStatus]]) -> None:
        self._scripted = {k: list(v) for k, v in scripted.items()}

    async def check(self, connection: Connection) -> HealthReport:
        queue = self._scripted.get(connection.name, [ConnectionStatus.HEALTHY])
        status = queue.pop(0) if queue else ConnectionStatus.HEALTHY
        return HealthReport(
            connection_name=connection.name,
            status=status,
            latency_ms=5.0,
            checked_at=datetime.now(UTC),
        )


def _make_connection(name: str) -> Connection:
    from synthorg.core.types import NotBlankStr
    from synthorg.integrations.connections.models import AuthMethod

    return Connection(
        name=NotBlankStr(name),
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        base_url=NotBlankStr("https://example.com"),
        health_check_enabled=True,
    )


@pytest.mark.integration
class TestHealthProberRegistry:
    def test_check_registry_covers_required_types(self) -> None:
        # Assert the required types are present rather than pinning the
        # registry at exactly N entries. Adding a sixth checker should
        # not break this test.
        required = {
            ConnectionType.GITHUB,
            ConnectionType.SLACK,
            ConnectionType.SMTP,
            ConnectionType.DATABASE,
            ConnectionType.GENERIC_HTTP,
        }
        assert required.issubset(_CHECK_REGISTRY.keys())

    def test_check_registry_is_read_only(self) -> None:
        assert isinstance(_CHECK_REGISTRY, MappingProxyType)

    def test_get_health_checker_returns_for_each_type(self) -> None:
        for connection_type in (
            ConnectionType.GITHUB,
            ConnectionType.SLACK,
            ConnectionType.SMTP,
            ConnectionType.DATABASE,
            ConnectionType.GENERIC_HTTP,
        ):
            assert get_health_checker(connection_type) is not None


@pytest.mark.integration
class TestHealthProberCycle:
    async def test_probe_all_updates_status_via_catalog(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        conn = _make_connection("probe-1")
        catalog = _FakeCatalog((conn,))
        clock = FakeClock()
        # _FakeCatalog is duck-typed to the catalog surface the prober uses;
        # the WARN-level checker rejects it against the concrete
        # ConnectionCatalog, so suppress the structural check at construction.
        with suppress_type_checks():
            svc = HealthProberService(
                catalog=catalog,  # type: ignore[arg-type]
                degraded_threshold=1,
                unhealthy_threshold=2,
                healthy_recheck_seconds=_RECHECK_SECONDS,
                degraded_recheck_seconds=_RECHECK_SECONDS,
                unhealthy_recheck_seconds=_RECHECK_SECONDS,
                clock=clock,
            )
        checker = _ScriptedChecker(
            {
                "probe-1": [
                    ConnectionStatus.UNHEALTHY,
                    ConnectionStatus.UNHEALTHY,
                    ConnectionStatus.HEALTHY,
                ]
            }
        )
        monkeypatch.setattr(prober_mod, "get_health_checker", lambda _: checker)

        # Each cycle writes a fresh verdict, so the escalation ladder is only
        # reachable by letting each one expire; probing sooner is the very
        # thing the recheck policy suppresses.
        # First probe: one failure -> DEGRADED.
        await svc._probe_all()
        clock.advance(_PAST_RECHECK_SECONDS)
        # Second probe: second failure -> UNHEALTHY.
        await svc._probe_all()
        clock.advance(_PAST_RECHECK_SECONDS)
        # Third probe: healthy -> back to HEALTHY, counter reset.
        await svc._probe_all()

        statuses = [status for _, status in catalog.updates]
        assert statuses == [
            ConnectionStatus.DEGRADED,
            ConnectionStatus.UNHEALTHY,
            ConnectionStatus.HEALTHY,
        ]

    async def test_healthy_probe_does_not_raise_unbound_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: HEALTHY path must not access ``count`` undefined."""
        conn = _make_connection("probe-healthy")
        catalog = _FakeCatalog((conn,))
        with suppress_type_checks():
            svc = HealthProberService(
                catalog=catalog,  # type: ignore[arg-type]
                degraded_threshold=1,
                unhealthy_threshold=3,
            )
        checker = _ScriptedChecker({"probe-healthy": [ConnectionStatus.HEALTHY]})
        monkeypatch.setattr(prober_mod, "get_health_checker", lambda _: checker)
        await svc._probe_all()
        assert catalog.updates == [("probe-healthy", ConnectionStatus.HEALTHY)]

    async def test_unknown_probe_does_not_escalate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A checker returning UNKNOWN (nothing to probe) never escalates.

        A cloud LLM provider with no base_url reports UNKNOWN every cycle;
        counting it as a failure would walk a perfectly healthy provider up to
        UNHEALTHY over successive cycles.
        """
        conn = _make_connection("probe-unknown")
        catalog = _FakeCatalog((conn,))
        clock = FakeClock()
        with suppress_type_checks():
            svc = HealthProberService(
                catalog=catalog,  # type: ignore[arg-type]
                degraded_threshold=1,
                unhealthy_threshold=2,
                healthy_recheck_seconds=_RECHECK_SECONDS,
                degraded_recheck_seconds=_RECHECK_SECONDS,
                unhealthy_recheck_seconds=_RECHECK_SECONDS,
                clock=clock,
            )
        checker = _ScriptedChecker({"probe-unknown": [ConnectionStatus.UNKNOWN] * 3})
        monkeypatch.setattr(prober_mod, "get_health_checker", lambda _: checker)
        await svc._probe_all()
        clock.advance(_PAST_RECHECK_SECONDS)
        await svc._probe_all()
        clock.advance(_PAST_RECHECK_SECONDS)
        await svc._probe_all()
        statuses = [status for _, status in catalog.updates]
        # Pinned to UNKNOWN rather than merely "not escalated": three cycles
        # actually ran, and a prober that quietly reported them HEALTHY would
        # satisfy a non-escalation check while inventing a verdict.
        assert statuses == [ConnectionStatus.UNKNOWN] * 3
