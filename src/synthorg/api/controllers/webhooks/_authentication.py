# module-kind: service
"""Everything ingest does before it believes a delivery.

Connection lookup, signature verification, and the sender's own delivery id.
Separated from the dedup and publish half because the two answer different
questions and only this one may reveal anything to an unauthenticated caller:
every refusal here answers the same 401 (:data:`UNVERIFIABLE_DELIVERY`), and
keeping them in one module is what makes that reviewable at a glance rather than
by grepping a longer file for every raise.
"""

from typing import Final

from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.core.domain_errors import UnauthorizedError
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.field_metadata import (
    WEBHOOK_SIGNING_SECRET_FIELD,
    get_connection_type_metadata,
)
from synthorg.integrations.connections.models import Connection, ConnectionType
from synthorg.integrations.errors import WebhookVerifierUnavailableError
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.integrations.webhooks.verifiers.factory import get_verifier
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import WEBHOOK_REJECTED

logger = get_logger(__name__)

#: The single message every unauthenticated rejection carries.
#:
#: Ingest is reachable without credentials, so any distinction between "no such
#: connection", "this type has no verifier", "the secret is unset" and "the
#: signature did not match" is an oracle: an unauthenticated caller could
#: enumerate connection names and learn, per name, whether a signing secret is
#: configured. The distinction is kept in the structured log, where an operator
#: can see it and an attacker cannot.
UNVERIFIABLE_DELIVERY: Final[str] = (
    "Webhook delivery could not be authenticated; request rejected"
)


async def get_verified_connection(state: State, connection_name: str) -> Connection:
    """Look up the named connection, rejecting an unknown name as unauthorised.

    Returns:
        ``Connection`` instance.

    Raises:
        UnauthorizedError: When no such connection exists. Deliberately not a
            404: see :data:`UNVERIFIABLE_DELIVERY`.
    """
    catalog: ConnectionCatalog = require_service(
        state["app_state"].slice(IntegrationsStateSlice).connection_catalog,
        "Connection Catalog",
    )
    conn = await catalog.get(connection_name)
    if conn is None:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="connection not found",
        )
        raise UnauthorizedError(UNVERIFIABLE_DELIVERY)
    return conn


async def verify_signature(
    *,
    catalog: ConnectionCatalog,
    connection: Connection,
    body: bytes,
    headers: dict[str, str],
) -> None:
    """Verify the webhook signature, raising 401 on missing secret or mismatch.

    Reads exactly one credential key, ``signing_secret``, the one the connection
    registry declares. Honouring a second undeclared name would open an ingest
    path no metadata-driven surface can see: the dashboard form and
    ``webhook_secret_field`` name only the declared field, and
    ``reject_inline_secret_fields`` can only refuse keys the registry knows are
    secret, so an undeclared alias could be posted inline through the create
    body and never appear as a credential anywhere an operator looks.

    Whitespace is stripped before the emptiness test: a blank-but-present secret
    is not a secret, and passing it through would hand the verifier a key an
    attacker can guess in one attempt.

    The secret field's own ``visible_when`` is resolved against the connection's
    stored values, so a secret captured while the field applied stops
    authenticating once it no longer does. Otherwise an operator who repointed a
    ``generic_http`` connection at a vendor preset would have retired the inbound
    path in every surface they can see while it kept publishing verified events.

    Raises:
        UnauthorizedError: Raised on the corresponding failure path.
    """
    connection_name = connection.name
    connection_type = connection.connection_type
    metadata = get_connection_type_metadata(connection_type)
    if not metadata.webhook_ingest_is_reachable(connection.metadata):
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            connection_type=connection_type.value,
            reason="signing secret does not apply to this connection",
        )
        raise UnauthorizedError(UNVERIFIABLE_DELIVERY)
    try:
        verifier = get_verifier(connection_type)
    except WebhookVerifierUnavailableError:
        # Collapsed into the same 401 rather than surfacing 501: a distinct
        # status tells an unauthenticated caller that the connection exists and
        # which types are ingest-capable.
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            connection_type=connection_type.value,
            reason="no verifier registered for connection type",
        )
        raise UnauthorizedError(UNVERIFIABLE_DELIVERY) from None
    credentials = await catalog.get_credentials(connection_name)
    signing_secret = credentials.get(WEBHOOK_SIGNING_SECRET_FIELD, "").strip()
    if not signing_secret:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="signing secret not configured",
        )
        raise UnauthorizedError(UNVERIFIABLE_DELIVERY)
    valid = await verifier.verify(
        body=body,
        headers=headers,
        secret=signing_secret,
    )
    if not valid:
        logger.warning(
            WEBHOOK_REJECTED,
            connection_name=connection_name,
            reason="signature verification failed",
        )
        raise UnauthorizedError(UNVERIFIABLE_DELIVERY)


def read_delivery_id(
    headers: dict[str, str],
    connection_type: ConnectionType,
) -> str | None:
    """Read the sender's own delivery id, for logging only.

    Each provider names it differently, so the verifier declares the header;
    ``None`` for a scheme that sends none. Not used for deduplication: the id is
    outside the signature and therefore attacker-controlled, which is why dedup
    keys on the delivery identity (connection plus body digest) instead.

    Returns:
        The trimmed delivery id, or ``None`` when absent or unsupported.
    """
    try:
        header = get_verifier(connection_type).delivery_id_header
    except WebhookVerifierUnavailableError:  # pragma: no cover -- verified first
        return None
    if header is None:
        return None
    return (headers.get(header) or "").strip() or None
