# module-kind: code
"""Secret-handling helpers for the connections controller.

Extracted from ``connections.py`` so the controller module stays within
its size budget. Covers the scoped credential reveal (uniform-404 on any
miss, audit-logged by field name only) and the out-of-band write-only
capture (raw value straight to the secret backend, opaque handle out).
"""

from synthorg._core.features import require_service
from synthorg.api.controllers.connections_models import (
    RevealedSecretResponse,
    SecretCaptureRequest,
    SecretCaptureResponse,
)
from synthorg.api.state import AppState
from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
    SecretRetrievalError,
    SecretRetrievalNotFoundError,
)
from synthorg.integrations.state import (
    IntegrationsStateSlice,
    secret_capture_service_of,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
)
from synthorg.observability.events.security import (
    SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
    SECURITY_CONNECTION_SECRET_REVEALED,
)

logger = get_logger(__name__)

# Unified error surfaced to clients on any reveal failure. Deliberately
# opaque so a caller cannot distinguish "connection missing" from "field
# missing" from "secret backend unavailable" and enumerate connections.
_REVEAL_GENERIC_ERROR = "Connection or credential field not found"


async def reveal_secret_field(
    app_state: AppState,
    name: str,
    field: str,
) -> RevealedSecretResponse:
    """Return the plaintext value of one credential field.

    Every miss (missing connection, unset field, or secret-backend failure)
    surfaces through one deliberate uniform 404 so the error cannot be used
    to enumerate which connections exist. The reveal is audit-logged by
    field name only, never the value.

    Returns:
        A ``RevealedSecretResponse`` carrying the single field's value.

    Raises:
        SecretRetrievalNotFoundError: On any reveal miss (uniform 404).
    """
    catalog = require_service(
        app_state.slice(IntegrationsStateSlice).connection_catalog,
        "Connection Catalog",
    )
    try:
        credentials = await catalog.get_credentials(name)
    except ConnectionNotFoundError as exc:
        logger.warning(
            SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
            connection=name,
            field=field,
            reason="connection_not_found",
        )
        raise SecretRetrievalNotFoundError(_REVEAL_GENERIC_ERROR) from exc
    except SecretRetrievalError as exc:
        # Backend failures are operational, not "not found"; log at ERROR
        # (redacted, no exc_info: a credential-op traceback can leak backend
        # secret metadata) yet still return the uniform 404 so the backend
        # error code cannot enumerate which connections exist.
        log_exception_redacted(
            logger,
            SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
            exc,
            connection=name,
            field=field,
            reason="secret_retrieval_failed",
        )
        raise SecretRetrievalNotFoundError(_REVEAL_GENERIC_ERROR) from exc

    value = credentials.get(field)
    if value is None:
        logger.warning(
            SECURITY_CONNECTION_SECRET_REVEAL_FAILED,
            connection=name,
            field=field,
            reason="field_not_set",
        )
        raise SecretRetrievalNotFoundError(_REVEAL_GENERIC_ERROR)
    logger.info(
        SECURITY_CONNECTION_SECRET_REVEALED,
        connection=name,
        field=field,
    )
    return RevealedSecretResponse(field=NotBlankStr(field), value=value)


async def capture_secret_value(
    app_state: AppState,
    draft_id: str,
    field: str,
    data: SecretCaptureRequest,
) -> SecretCaptureResponse:
    """Capture a credential value out of band and return an opaque handle.

    The raw value is written straight to the secret backend and never enters
    the conversation transcript, an LLM prompt, or the logs; the returned
    single-use handle is consumed once by ``connections.create``.

    Returns:
        A ``SecretCaptureResponse`` wrapping the opaque handle.
    """
    service = secret_capture_service_of(app_state)
    handle = await service.capture(
        draft_id=NotBlankStr(draft_id),
        field_name=NotBlankStr(field),
        secret_kind=data.secret_kind,
        value=data.value.get_secret_value(),
        conversation_id=data.conversation_id,
    )
    return SecretCaptureResponse(handle=handle)
