"""An edit that redefines the probe must not keep the old verdict.

The aggregate-health endpoint serves the stored verdict rather than probing
on every request, and a healthy one is trusted for hours. That is only safe
while the verdict describes the connection as it is now: repoint a
connection's ``base_url`` and the recorded HEALTHY stops being stale and
starts being wrong, because it is evidence about an endpoint the connection
no longer uses.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections._update_pipeline import materialise_update
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionHealth,
    ConnectionStatus,
    ConnectionType,
    WebhookIngestState,
)

pytestmark = pytest.mark.unit


def _healthy_connection() -> Connection:
    return Connection(
        name=NotBlankStr("c1"),
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        base_url=NotBlankStr("https://old.example.test"),
        health=ConnectionHealth(
            status=ConnectionStatus.HEALTHY,
            last_check_at=datetime.now(UTC),
            detail=None,
            latency_ms=12.0,
            webhook_ingest=WebhookIngestState.READY,
        ),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", NotBlankStr("https://new.example.test")),
        ("auth_method", AuthMethod.BEARER_TOKEN),
        ("metadata", {"vendor": "custom"}),
    ],
)
def test_probe_defining_edit_clears_the_verdict(field: str, value: object) -> None:
    updated = materialise_update(_healthy_connection(), {field: value})
    assert updated is not None
    assert updated.health.status is ConnectionStatus.UNKNOWN
    assert updated.health.last_check_at is None
    # The whole verdict goes, not just the headline: keeping a latency or an
    # ingest state measured against the old configuration would describe a
    # connection that no longer exists.
    assert updated.health.latency_ms is None
    assert updated.health.webhook_ingest is WebhookIngestState.NOT_APPLICABLE


def test_unrelated_edit_keeps_the_verdict() -> None:
    # Renaming what a connection is called changes nothing a probe measures,
    # so discarding a good verdict would buy a needless billed re-probe.
    existing = _healthy_connection()
    updated = materialise_update(existing, {"sensitive": True})
    assert updated is not None
    assert updated.health.status is ConnectionStatus.HEALTHY
    assert updated.health.latency_ms == pytest.approx(12.0)


def test_no_change_is_still_no_update() -> None:
    existing = _healthy_connection()
    assert materialise_update(existing, {"base_url": existing.base_url}) is None
