"""Shared health check helper.

Provides ``check_connection_health`` used by both the per-connection
endpoint on ``ConnectionsController`` and the aggregate endpoint on
``IntegrationHealthController``.
"""

from datetime import UTC, datetime

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.field_metadata import (
    WEBHOOK_SIGNING_SECRET_FIELD,
    get_connection_type_metadata,
)
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    WebhookIngestState,
)
from synthorg.integrations.health.models import HealthReport
from synthorg.integrations.health.prober import get_health_checker
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import HEALTH_CHECK_FAILED

logger = get_logger(__name__)


async def _webhook_ingest_state(
    catalog: ConnectionCatalog,
    conn: Connection,
) -> WebhookIngestState:
    """Report whether inbound deliveries to *conn* can be authenticated.

    Derived from configuration rather than probed: ingest rejects a delivery it
    cannot authenticate, and the reasons it cannot are exactly the ones checked
    here, in the same order the ingest path checks them. Reading the credential
    only after the applicability test keeps a secret-backend round trip off every
    connection that has no inbound path anyway.

    Returns:
        ``NOT_APPLICABLE`` when the type declares no signing-secret field or the
        field's condition does not hold for this connection, ``UNCONFIGURED``
        when it applies but no usable secret is stored, else ``READY``.
    """
    metadata = get_connection_type_metadata(conn.connection_type)
    if not metadata.webhook_ingest_is_reachable(conn.metadata):
        return WebhookIngestState.NOT_APPLICABLE
    credentials = await catalog.get_credentials(conn.name)
    stored = credentials.get(WEBHOOK_SIGNING_SECRET_FIELD, "").strip()
    return WebhookIngestState.READY if stored else WebhookIngestState.UNCONFIGURED


async def check_connection_health(
    catalog: ConnectionCatalog,
    name: str,
) -> HealthReport:
    """Run an on-demand health check for a single connection.

    Args:
        catalog: The connection catalog.
        name: Connection name.

    Returns:
        A ``HealthReport`` with the check result. Inbound-webhook readiness rides
        along on every outcome, including the failures: an unset signing secret
        is a standing configuration fact, so withholding it because the outbound
        probe happened to fail would hide it exactly when an operator is looking.

    Raises:
        ConnectionNotFoundError: If the connection does not exist.
    """
    conn = await catalog.get_or_raise(name)
    checker = get_health_checker(conn.connection_type)
    now = datetime.now(UTC)
    ingest = await _webhook_ingest_state(catalog, conn)

    if checker is None:
        # Surface ``UNKNOWN`` instead of ``conn.health.status`` so an
        # on-demand caller never sees a stale persisted status (which
        # could otherwise report a long-dead integration as
        # ``HEALTHY``). The missing checker is logged so operators can
        # register one.
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=name,
            connection_type=(
                conn.connection_type.value
                if hasattr(conn.connection_type, "value")
                else str(conn.connection_type)
            ),
            error="no health checker registered for this type",
        )
        return HealthReport(
            connection_name=conn.name,
            status=ConnectionStatus.UNKNOWN,
            error_detail="No health checker for this type",
            checked_at=now,
            webhook_ingest=ingest,
        )

    try:
        report = await checker.check(conn)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return HealthReport(
            connection_name=conn.name,
            status=ConnectionStatus.UNHEALTHY,
            error_detail=safe_error_description(exc),
            checked_at=now,
            webhook_ingest=ingest,
        )
    # Attached here rather than inside each per-type checker: the state is the
    # same computation for every type, and a checker that forgot it would
    # silently report NOT_APPLICABLE on a connection that does have an inbound
    # path.
    return report.model_copy(update={"webhook_ingest": ingest})
