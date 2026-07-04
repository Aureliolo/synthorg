# module-kind: code
"""Shared, opt-in idempotency wrapper for the conversational chat endpoints.

The four mutating chat endpoints (``/meta/chat``, ``/propose``,
``/group``, ``/act``) accept an optional ``Idempotency-Key`` header. When
a caller attaches one, a 5xx/timeout-driven retry with the same key
replays the cached response instead of re-running the turn (and, for
``/act``, re-executing real MCP tool calls). The header is deliberately
optional: durable idempotency requires persistence, but explain-chat and
direct acting can run without it, so a caller opts in only when it wants
retry safety.

This centralises the header definition, the request fingerprint, and the
``run_idempotent`` plumbing so all four endpoints stay consistent.
"""

import hashlib
from collections.abc import Awaitable, Callable
from typing import Annotated, Final

from litestar.params import HeaderParameter
from pydantic import BaseModel

from synthorg.api.api_core_state import idempotency_service_of
from synthorg.api.state import AppState
from synthorg.core.domain_errors import ConflictError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT

logger = get_logger(__name__)

# The durable idempotency-key column is bounded at 255 chars. The chat
# endpoints store the caller's raw key (no path-embedded resource id to
# compose in), so the full 255 is available.
_MAX_CHAT_IDEMPOTENCY_KEY_LEN: Final[int] = 255

ChatIdempotencyKeyHeader = Annotated[
    NotBlankStr | None,
    HeaderParameter(
        name="Idempotency-Key",
        description=(
            "Optional RFC-style retry-safe key. When supplied, an identical "
            "key within the retention window returns the cached response "
            "instead of re-running the turn, so a 5xx/timeout-driven retry "
            "cannot double-fire the conversation (or, for /act, the tool "
            "calls). Durable idempotency requires a persistence backend."
        ),
        required=False,
        max_length=_MAX_CHAT_IDEMPOTENCY_KEY_LEN,
    ),
]


def chat_request_fingerprint(model: BaseModel) -> str:
    """Stable SHA-256 fingerprint of a chat request body.

    Lets the idempotency layer tell a genuine retry (identical payload)
    from a key reused for a different request. Pydantic emits fields in
    declaration order, so the dump is deterministic for a given model.

    Returns:
        Hex SHA-256 digest of the serialised model.
    """
    return hashlib.sha256(model.model_dump_json().encode("utf-8")).hexdigest()


async def run_chat_idempotent(  # noqa: PLR0913 -- idempotency plumbing seam
    app_state: AppState,
    *,
    scope: str,
    key: str | None,
    endpoint: str,
    request_fingerprint: str,
    build: Callable[[], Awaitable[BaseModel]],
) -> dict[str, object]:
    """Run *build* under the idempotency guard, returning its dumped dict.

    When *key* is ``None`` the caller did not opt in: *build* runs
    directly. Otherwise a fresh key runs *build* (the service call plus
    response construction) and caches its JSON dump, and a repeated key
    returns the cached dump. The caller re-validates the returned dict
    back into its typed ``ApiResponse``.

    Raises:
        ConflictError: When a concurrent in-flight call holds *key*.

    Returns:
        The fresh or cached response as a JSON-safe dict.
    """

    async def _dump() -> dict[str, object]:
        response = await build()
        # Exclude computed fields (e.g. ApiResponse.success) so the stored
        # JSON re-validates: computed fields are dumped but rejected by the
        # frozen ``extra="forbid"`` models on ``model_validate``, and they
        # are recomputed on reconstruction anyway.
        computed = set(type(response).model_computed_fields)
        return response.model_dump(mode="json", exclude=computed)

    if key is None:
        return await _dump()

    outcome = await idempotency_service_of(app_state).run_idempotent(
        scope=NotBlankStr(scope),
        key=NotBlankStr(key),
        callback=_dump,
        request_fingerprint=request_fingerprint,
    )
    if outcome.timed_out:
        logger.warning(
            IDEMPOTENCY_CLAIM_IN_FLIGHT,
            scope=scope,
            idempotency_key=key,
            endpoint=endpoint,
        )
        msg = "Concurrent in-flight chat request with this idempotency key"
        raise ConflictError(msg)
    result = outcome.result
    if not isinstance(result, dict):
        # The callback always returns a dict; a non-dict cached value
        # means a corrupt store entry. Fail closed rather than hand the
        # caller an unvalidatable body.
        msg = "Idempotency store returned a non-dict chat response"
        raise ConflictError(msg)
    return result
