"""Shared resolver-backed kill-switch helper.

Many subsystems advertise a boolean kill switch in the registry that
gates a hot-path call: evolution triggers, training ingestion, meeting
scheduling, evaluation metric recording, request rate limiting, memory
consolidation, escalation sweeping.  Each gate has the same shape --
read the flag once at the entry point, log a single
``KILL_SWITCH_ENGAGED`` audit line when the gate trips, fall back to a
safe default on resolver outage so a transient settings failure cannot
silently flip-flop subsystem behavior.

This helper concentrates that shape so per-subsystem gates stay terse
and consistent across the codebase.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)


async def resolve_bool_with_fallback(
    *,
    resolver: ConfigResolverProtocol | None,
    namespace: str,
    key: str,
    fallback: bool,
) -> bool:
    """Resolve a boolean setting through ``ConfigResolver`` with a fallback.

    Returns *fallback* immediately when the resolver is ``None``
    (subsystem not yet wired into AppState, test harness, anonymous
    boot path).  On resolver outage, logs a single
    ``SETTINGS_FETCH_FAILED`` warning and returns *fallback* so a
    transient settings failure cannot collapse a kill switch in either
    direction.

    Args:
        resolver: The application's config resolver, or ``None`` when
            the caller is not yet wired.
        namespace: Setting namespace (e.g. ``"engine"``).
        key: Setting key within the namespace.
        fallback: Value to return when no resolver is wired or the
            lookup fails.  **Callers must pass the same value as the
            registered ``SettingDefinition.default``**: a mismatch
            would cause divergent behaviour between resolver-up and
            resolver-down paths (an operator who sees the documented
            default in the registry would observe a different actual
            value during a settings outage).  This invariant is the
            caller's responsibility -- the helper has no way to look
            up the registered default itself.

    Returns:
        The resolved boolean, or *fallback* on missing resolver / outage.
    """
    if resolver is None:
        return fallback
    try:
        return await resolver.get_bool(namespace, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # reraise_critical re-raises MemoryError / RecursionError before any
        # logging or fallback runs. asyncio.CancelledError is a BaseException,
        # so this broad ``except Exception`` never catches it: an aborted await
        # propagates untouched rather than being masked as a settings outage.
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=namespace,
            key=key,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=fallback,
        )
        return fallback


async def resolve_str_with_fallback(
    *,
    resolver: ConfigResolverProtocol | None,
    namespace: str,
    key: str,
    fallback: str,
) -> str:
    """Resolve a string setting through ``ConfigResolver`` with a fallback.

    The string analogue of :func:`resolve_bool_with_fallback`, used for
    per-LLM-call model identifiers that should track a live ``/settings``
    override without a restart.  Returns *fallback* when the resolver is
    ``None`` (caller not yet wired / test harness), when the lookup fails,
    or when the resolved value is **blank** -- a blank model setting means
    "keep the built-in default", matching the overlay's skip-if-blank rule
    in :func:`synthorg.meta._config_overlay.overlay_feature_settings` so the
    live path and the boot overlay agree.

    Args:
        resolver: The application's config resolver, or ``None`` when the
            caller is not yet wired.
        namespace: Setting namespace (e.g. ``"chief_of_staff"``).
        key: Setting key within the namespace.
        fallback: Value to return when no resolver is wired, the lookup
            fails, or the resolved string is blank.  Callers pass the
            baked-config model so a settings outage cannot silently swap
            the active model.

    Returns:
        The resolved non-blank string, or *fallback* otherwise.
    """
    if resolver is None:
        return fallback
    try:
        resolved = await resolver.get_str(namespace, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=namespace,
            key=key,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=fallback,
        )
        return fallback
    return resolved if resolved.strip() else fallback


async def resolve_float_with_fallback(
    *,
    resolver: ConfigResolverProtocol | None,
    namespace: str,
    key: str,
    fallback: float,
) -> float:
    """Resolve a float setting through ``ConfigResolver`` with a fallback.

    The float sibling of :func:`resolve_bool_with_fallback`, for per-tick
    re-reads of operator-tunable cadence / window knobs (e.g. a scheduler
    interval). Returns *fallback* immediately when the resolver is ``None``,
    and on a resolver outage logs a single ``SETTINGS_FETCH_FAILED`` warning and
    returns *fallback* so a transient settings failure cannot wedge the caller.

    Args:
        resolver: The application's config resolver, or ``None`` when the caller
            is not yet wired.
        namespace: Setting namespace (e.g. ``"hr"``).
        key: Setting key within the namespace.
        fallback: Value to return when no resolver is wired or the lookup fails.
            Pass the construction-time value (the last-known-good cadence) so the
            resolver-up and resolver-down paths stay consistent.

    Returns:
        The resolved float, or *fallback* on missing resolver / outage.
    """
    if resolver is None:
        return fallback
    try:
        return await resolver.get_float(namespace, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=namespace,
            key=key,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=fallback,
        )
        return fallback


async def resolve_int_with_fallback(
    *,
    resolver: ConfigResolverProtocol | None,
    namespace: str,
    key: str,
    fallback: int,
) -> int:
    """Resolve an int setting through ``ConfigResolver`` with a fallback.

    The int sibling of :func:`resolve_float_with_fallback`, for per-call
    re-reads of operator-tunable size / count knobs (e.g. a per-batch cap).
    Returns *fallback* immediately when the resolver is ``None``, and on a
    resolver outage logs a single ``SETTINGS_FETCH_FAILED`` warning and returns
    *fallback* so a transient settings failure cannot wedge the caller.

    Args:
        resolver: The application's config resolver, or ``None`` when the caller
            is not yet wired.
        namespace: Setting namespace (e.g. ``"memory"``).
        key: Setting key within the namespace.
        fallback: Value to return when no resolver is wired or the lookup fails.
            Pass the construction-time value so resolver-up / resolver-down paths
            stay consistent.

    Returns:
        The resolved int, or *fallback* on missing resolver / outage.
    """
    if resolver is None:
        return fallback
    try:
        return await resolver.get_int(namespace, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=namespace,
            key=key,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=fallback,
        )
        return fallback


# Upper bound on a live-resolved model identifier. A real model id is a short
# ``provider/model:tag`` token; anything longer is a malformed / injected value.
_MAX_MODEL_ID_LEN: Final[int] = 256


def _is_clean_model_id(value: str) -> bool:
    """Whether *value* is a structurally plausible model identifier.

    A model id is a single printable token with no whitespace at all
    (surrounding or embedded) and within a sane length bound.
    ``str.isprintable`` rejects every control character (newlines, tabs,
    NUL); the explicit whitespace check additionally rejects an embedded
    space (e.g. ``"provider/ model"``) that ``isprintable`` would let
    through. This is a sanity guard against a corrupted settings store, NOT
    a provider allowlist: operators legitimately set arbitrary custom model
    strings, so any clean single token passes.

    Returns:
        ``True`` when *value* is a clean model identifier.
    """
    return (
        bool(value)
        and not any(ch.isspace() for ch in value)
        and len(value) <= _MAX_MODEL_ID_LEN
        and value.isprintable()
    )


async def resolve_model_with_fallback(
    *,
    resolver: ConfigResolverProtocol | None,
    namespace: str,
    key: str,
    fallback: str,
) -> str:
    """Resolve a live model identifier, falling back on a malformed value.

    Wraps :func:`resolve_str_with_fallback` and additionally rejects a
    resolved value that is not a clean single-line model identifier,
    returning the baked *fallback* instead (with a warning). The resolved
    model feeds straight into a provider call, so a corrupted settings store
    must not inject a control-laden or oversized string into that boundary.
    A blank / missing / outage value already collapses to *fallback* upstream.

    Args:
        resolver: The application's config resolver, or ``None`` when the
            caller is not yet wired.
        namespace: Setting namespace (e.g. ``"chief_of_staff"``).
        key: Model setting key within the namespace.
        fallback: Baked-config model returned when no resolver is wired, the
            lookup fails, the value is blank, or the value is malformed.

    Returns:
        The resolved clean model identifier, or *fallback* otherwise.
    """
    resolved = await resolve_str_with_fallback(
        resolver=resolver, namespace=namespace, key=key, fallback=fallback
    )
    if resolved == fallback or _is_clean_model_id(resolved):
        return resolved
    logger.warning(
        SETTINGS_FETCH_FAILED,
        namespace=namespace,
        key=key,
        error_type="MalformedModelIdentifier",
        error="resolved model identifier failed structural validation",
        fallback=fallback,
    )
    return fallback


def require_configured_model(
    model: str | None,
    *,
    namespace: str,
    key: str,
    feature_label: str,
) -> str:
    """Return *model* when configured; raise a settings-pointing 503 otherwise.

    A per-feature model that resolves blank means no model has been selected
    yet (no placeholder is ever shipped as a default). Rather than call a
    provider with an empty model identifier, surface a clear
    ``ServiceUnavailableError`` naming the setting the operator must configure.

    Args:
        model: The live-resolved model identifier, possibly blank/``None``.
        namespace: Setting namespace (e.g. ``"chief_of_staff"``).
        key: Model setting key within the namespace.
        feature_label: Human-readable capability name for the 503 message.

    Returns:
        The non-blank *model* identifier.

    Raises:
        ServiceUnavailableError: When *model* is blank or ``None``.
    """
    if model:
        return model
    msg = (
        f"{feature_label} has no model configured. Set ``{namespace}.{key}``"
        " in dashboard Settings."
    )
    raise ServiceUnavailableError(msg)
