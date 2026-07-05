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


type ExcludeMap = dict[str, bool | ExcludeMap]


def _computed_field_exclude(model: BaseModel) -> ExcludeMap:
    """Nested Pydantic ``exclude`` mapping over every computed field.

    Computed fields are dumped by ``model_dump`` but rejected by the frozen
    ``extra="forbid"`` result models on ``model_validate`` (and recomputed on
    reconstruction anyway), so the cached idempotency payload must omit them
    at *every* level, not just the top: ``ConversationalActResult.action`` is
    a ``ChatActionResult`` whose ``parked`` is a computed field.

    Returns:
        A mapping suitable for ``model_dump(exclude=...)``.
    """
    exclude: ExcludeMap = {}
    for name in type(model).model_computed_fields:
        exclude[name] = True
    for name in type(model).model_fields:
        nested = _nested_exclude(getattr(model, name))
        if nested:
            exclude[name] = nested
    return exclude


def _nested_exclude(value: object) -> ExcludeMap | None:
    """Exclusion sub-mapping for one field value, or ``None`` when none applies.

    Returns:
        The nested ``exclude`` mapping, or ``None`` for a leaf value.
    """
    if isinstance(value, BaseModel):
        return _computed_field_exclude(value) or None
    if isinstance(value, list | tuple):
        for item in value:
            sub = _nested_exclude(item)
            # Typed sequences are homogeneous, so the first item that carries
            # computed fields reveals the shape ``__all__`` applies to all of.
            if sub:
                return {"__all__": sub}
    return None


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
    actor_id: str,
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

    The cache is partitioned by *actor_id* (folded into the scope) so a
    key + body a non-dashboard API caller happens to share with another
    caller cannot cross-return one caller's ``conversation_id`` /
    ``approval_id`` to the other.

    Raises:
        ConflictError: When a concurrent in-flight call holds *key*.

    Returns:
        The fresh or cached response as a JSON-safe dict.
    """

    async def _dump() -> dict[str, object]:
        response = await build()
        # Exclude computed fields recursively (e.g. ApiResponse.success and the
        # nested ChatActionResult.parked) so the stored JSON re-validates
        # against the frozen ``extra="forbid"`` models on ``model_validate``.
        return response.model_dump(
            mode="json", exclude=_computed_field_exclude(response)
        )

    if key is None:
        return await _dump()

    outcome = await idempotency_service_of(app_state).run_idempotent(
        scope=NotBlankStr(f"{scope}:{actor_id}"),
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
