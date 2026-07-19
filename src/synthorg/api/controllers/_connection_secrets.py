# module-kind: code
"""Secret-handling helpers for the connections controller.

Extracted from ``connections.py`` so the controller module stays within
its size budget. Covers the scoped credential reveal (uniform-404 on any
miss, audit-logged by field name only) and the out-of-band write-only
capture (raw value straight to the secret backend, opaque handle out).
"""

from synthorg._core.features import require_service
from synthorg.api.controllers.connections_models import (
    CreateConnectionRequest,
    RevealedSecretResponse,
    SecretCaptureRequest,
    SecretCaptureResponse,
)
from synthorg.api.state import AppState
from synthorg.core.domain_errors import ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.secret_capture import resolve_credential_handles
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


_DRAFT_ID_REQUIRED = (
    "connection_draft_id is required when credential_handles are supplied"
)


async def resolve_create_credentials(
    app_state: AppState,
    data: CreateConnectionRequest,
) -> dict[str, str]:
    """Resolve a create request's credentials, consuming any secret handles.

    Inline ``credentials`` (non-secret fields) merge with secret fields
    resolved out of band from ``credential_handles``. The raw secret value is
    consumed once, in-process, against its ``(connection_draft_id, field)``
    binding and never enters the request body or the logs.

    Returns:
        The full credentials mapping ready for ``catalog.create``.

    Raises:
        ValidationError: If handles are supplied without a connection_draft_id.
        SecretCaptureHandleInvalidError: If a handle is invalid or expired.
    """
    if not data.credential_handles:
        return dict(data.credentials)
    if data.connection_draft_id is None:
        raise ValidationError(_DRAFT_ID_REQUIRED)
    return await resolve_credential_handles(
        secret_capture_service_of(app_state),
        credentials=dict(data.credentials),
        credential_handles=dict(data.credential_handles),
        connection_draft_id=data.connection_draft_id,
    )


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
