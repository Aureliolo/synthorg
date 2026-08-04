# module-kind: code
"""Resolve a consumer's own ``(provider, model)`` pair, or nothing at all.

Every LLM dispatch names both halves. A provider here is a registered
*connection*, carrying its own credentials, endpoint and quota, so the same
model id reached through two connections is two different calls, billed and
rate-limited separately. A bare model id therefore names no dispatch target,
and there is no shared default to borrow: a consumer either has its own pair
or it is off.

This is the single read path for that. It returns ``None`` for an unset value,
for half a pair, and for a read failure, logging each under the caller's own
event so an unarmed feature is visible rather than silently inert.
"""

from synthorg.api.state import AppState
from synthorg.observability import get_logger, safe_error_description
from synthorg.settings.errors import SettingsError
from synthorg.settings.model_ref import ModelRef, parse_model_ref
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


async def resolve_bound_model(
    app_state: AppState,
    *,
    namespace: str,
    key: str,
    unset_event: str,
) -> ModelRef | None:
    """Read *namespace.key* as an explicitly bound provider + model pair.

    Args:
        app_state: Application state carrying the settings resolver.
        namespace: Settings namespace holding the model reference.
        key: The ``MODEL_REF`` setting key.
        unset_event: Event name to log an unresolved pair under, so each
            caller's observability stays in its own event family.

    Returns:
        The bound :class:`ModelRef`, or ``None`` when the operator has not
        chosen one, chose only half of one, or the read failed.
    """
    try:
        raw = await config_resolver_of(app_state).get_str(namespace, key)
    except (SettingsError, ValueError) as exc:
        logger.warning(
            unset_event,
            setting=f"{namespace}.{key}",
            reason="config_resolve_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    ref = parse_model_ref(raw)
    if not ref.is_bound:
        logger.warning(
            unset_event,
            setting=f"{namespace}.{key}",
            reason="unbound_model_ref",
            has_provider=bool(ref.provider.strip()),
            has_model=bool(ref.model_id.strip()),
        )
        return None
    return ref
