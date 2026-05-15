"""Well-known Agent Card endpoints.

Serves Agent Cards at ``/.well-known/agent-card.json`` (company
level) and ``/.well-known/agents/{agent_id}/agent-card.json``
(per-agent).  These endpoints are unauthenticated per the A2A
spec -- Agent Cards are public discovery documents.

Registered at the Litestar root level (outside ``/api/v1``) and
only mounted when ``a2a.enabled = True``.
"""

import asyncio
import hashlib
from typing import Any

from litestar import Controller, Request, get
from litestar.datastructures import State  # noqa: TC002
from litestar.response import Response

from synthorg.a2a.agent_card import AgentCardBuilder  # noqa: TC001
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.normalization import strip_trailing_slash
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.a2a import (
    A2A_AGENT_CARD_CACHE_HIT,
    A2A_AGENT_CARD_CACHE_MISS,
    A2A_AGENT_CARD_SERVED,
)
from synthorg.settings.errors import SettingNotFoundError

logger = get_logger(__name__)

# Module-level cache: (card_data, expires_at, fingerprint).
_card_cache: dict[str, tuple[dict[str, Any], float, str]] = {}
_cache_lock = asyncio.Lock()
# Module-level clock singleton; tests inject a FakeClock by passing
# it explicitly to the cache helpers below.
_default_clock: Clock = SystemClock()


async def _get_cached_card(
    cache_key: str,
    ttl: int,
    *,
    fingerprint: str = "",
    clock: Clock | None = None,
) -> dict[str, Any] | None:
    """Return cached card data if still valid.

    Args:
        cache_key: Cache key (scoped by host + agent/company).
        ttl: Cache TTL in seconds (0 disables caching).
        fingerprint: Identity fingerprint -- when provided, the
            cached entry is invalidated if the fingerprint changed.
        clock: Time source override (defaults to module-level
            ``_default_clock``); tests inject a FakeClock to drive
            cache expiry deterministically.

    Returns:
        Cached card dict or None if expired/missing/stale.
    """
    if ttl <= 0:
        return None
    active_clock = clock or _default_clock
    async with _cache_lock:
        entry = _card_cache.get(cache_key)
        if entry is None:
            return None
        card_data, expires_at, stored_fp = entry
        if active_clock.monotonic() > expires_at:
            del _card_cache[cache_key]
            return None
        if fingerprint and stored_fp != fingerprint:
            del _card_cache[cache_key]
            return None
        return card_data


async def _put_cached_card(
    cache_key: str,
    card_data: dict[str, Any],
    ttl: int,
    *,
    fingerprint: str = "",
    clock: Clock | None = None,
) -> None:
    """Store card data in cache with TTL and fingerprint.

    Args:
        cache_key: Cache key.
        card_data: Serialized card dict.
        ttl: TTL in seconds (0 skips caching).
        fingerprint: Identity fingerprint for staleness detection.
        clock: Time source override (defaults to module-level
            ``_default_clock``); tests inject a FakeClock to control
            the stored expiry deadline.
    """
    if ttl <= 0:
        return
    active_clock = clock or _default_clock
    async with _cache_lock:
        _card_cache[cache_key] = (
            card_data,
            active_clock.monotonic() + ttl,
            fingerprint,
        )


async def _resolve_company_name(app_state: Any) -> str:
    """Read ``company.company_name`` through ``ConfigResolver`` with fallback.

    A ``/settings/company/company_name`` runtime write only reaches this
    endpoint when the resolver is consulted per request; capturing the
    value at boot-time would freeze it on the running process. The
    fallback to ``app_state.config.company_name`` keeps ``.well-known``
    serving on a settings-backend outage instead of 500'ing.

    ``SettingNotFoundError`` is a quiet fallback because it is a normal
    initial state on fresh installs before setup has registered the
    key. The broader ``Exception`` catch logs WARNING with safe error
    description; ``MemoryError`` and ``RecursionError`` propagate per
    the surrounding controller's convention.
    """
    try:
        resolved = await app_state.config_resolver.get_str("company", "company_name")
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return str(app_state.config.company_name)
    except Exception as exc:
        logger.warning(
            A2A_AGENT_CARD_SERVED,
            card_type="company",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            reason="company_name_resolver_failed_using_snapshot_fallback",
        )
        return str(app_state.config.company_name)
    return str(resolved)


def _card_response(card_data: dict[str, Any], ttl: int) -> Response[dict[str, Any]]:
    """Build the success JSON response with the public cache header."""
    return Response(
        content=card_data,
        media_type="application/json",
        headers={"Cache-Control": f"public, max-age={ttl}"},
    )


def _service_unavailable_response() -> Response[dict[str, Any]]:
    """Build the 503 response served when card assembly fails."""
    return Response(
        content={"error": "Service temporarily unavailable"},
        media_type="application/json",
        status_code=503,
    )


async def _assemble_company_card(
    app_state: Any,
    base_url: str,
    company_name: str,
) -> tuple[dict[str, Any], int]:
    """Build the company card payload for an already-resolved name.

    Returns ``(card_data, agent_count)``. Raises on any failure; the
    caller maps that to a 503.
    """
    builder: AgentCardBuilder = app_state.a2a_card_builder
    registry = app_state.agent_registry
    identities = await registry.list_active()
    card = builder.build_company_card(
        identities=identities,
        base_url=f"{base_url}/api/v1/a2a",
        company_name=company_name,
    )
    return card.model_dump(), len(identities)


async def _resolve_agent_for_card(app_state: Any, agent_id: str) -> Any | None:
    """Resolve an agent identity by id, then by name.

    Returns the identity, or ``None`` when no agent matches
    ``agent_id`` (caller maps that to 404). Raises on registry
    failure; the caller maps that to a 503.
    """
    registry = app_state.agent_registry
    identity = await registry.get(agent_id)
    if identity is None:
        identity = await registry.get_by_name(agent_id)
    return identity


def _agent_fingerprint(identity: Any) -> str:
    """Compute the staleness fingerprint for an agent identity.

    Derived from name + role + skills so a rename, role change, or
    skill edit invalidates a cached card before TTL expiry instead
    of serving a stale document.
    """
    return hashlib.sha256(
        f"{identity.name}:{identity.role}:{identity.skills}".encode(),
    ).hexdigest()[:16]


def _build_agent_card_payload(
    app_state: Any,
    identity: Any,
    host_base: str,
) -> dict[str, Any]:
    """Build the card payload for an already-resolved identity."""
    builder: AgentCardBuilder = app_state.a2a_card_builder
    card = builder.build(
        identity=identity,
        base_url=f"{host_base}/api/v1/a2a",
    )
    return card.model_dump()


class WellKnownAgentCardController(Controller):
    """Serves A2A Agent Cards at well-known URIs."""

    path = "/.well-known"
    tags = ["A2A"]  # noqa: RUF012

    @get(
        "/agent-card.json",
        summary="Company-level Agent Card",
        description=(
            "Returns an aggregated Agent Card representing "
            "all agents in this organization."
        ),
    )
    async def company_agent_card(
        self,
        state: State,
        request: Request[Any, Any, Any],
    ) -> Response[dict[str, Any]]:
        """Serve the company-level Agent Card."""
        app_state = state["app_state"]
        ttl = app_state.config.a2a.agent_card_cache_ttl_seconds

        base_url = strip_trailing_slash(str(request.base_url))
        # Resolve company_name before the cache read and key on it so a
        # runtime write to ``company.company_name`` (DB-tier override
        # per the configuration-precedence contract) takes effect
        # immediately instead of being hidden until TTL expiry. Stale
        # entries for prior names age out by TTL. Agent-list staleness
        # remains TTL-bounded by design.
        company_name = await _resolve_company_name(app_state)
        cache_key = f"__company__:{base_url}:{company_name}"
        cached = await _get_cached_card(cache_key, ttl)
        if cached is not None:
            logger.debug(A2A_AGENT_CARD_CACHE_HIT, cache_key=cache_key)
            return _card_response(cached, ttl)

        logger.debug(A2A_AGENT_CARD_CACHE_MISS, cache_key=cache_key)

        try:
            card_data, agent_count = await _assemble_company_card(
                app_state,
                base_url,
                company_name,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                A2A_AGENT_CARD_SERVED,
                card_type="company",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="company_agent_card_build_failed",
            )
            return _service_unavailable_response()

        await _put_cached_card(cache_key, card_data, ttl)
        logger.info(
            A2A_AGENT_CARD_SERVED,
            card_type="company",
            agent_count=agent_count,
        )
        return _card_response(card_data, ttl)

    @get(
        "/agents/{agent_id:str}/agent-card.json",
        summary="Per-agent Agent Card",
        description=(
            "Returns the Agent Card for a specific agent identified by agent_id."
        ),
    )
    async def agent_card(
        self,
        state: State,
        request: Request[Any, Any, Any],
        agent_id: str,
    ) -> Response[dict[str, Any]]:
        """Serve a per-agent Agent Card."""
        from synthorg.core.domain_errors import NotFoundError  # noqa: PLC0415

        app_state = state["app_state"]
        ttl = app_state.config.a2a.agent_card_cache_ttl_seconds

        host_base = strip_trailing_slash(str(request.base_url))
        cache_key = f"{agent_id}:{host_base}"

        try:
            identity = await _resolve_agent_for_card(app_state, agent_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                A2A_AGENT_CARD_SERVED,
                card_type="agent",
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="agent_identity_resolution_failed",
            )
            return _service_unavailable_response()

        if identity is None:
            msg = f"Agent '{agent_id}' not found"
            raise NotFoundError(msg)

        # Resolve identity before the cache read so the current
        # fingerprint gates the lookup: a rename, role change, or
        # skill edit invalidates the cached card immediately instead
        # of being served stale until TTL expiry.
        fingerprint = _agent_fingerprint(identity)
        cached = await _get_cached_card(cache_key, ttl, fingerprint=fingerprint)
        if cached is not None:
            logger.debug(A2A_AGENT_CARD_CACHE_HIT, cache_key=cache_key)
            return _card_response(cached, ttl)

        logger.debug(A2A_AGENT_CARD_CACHE_MISS, cache_key=cache_key)

        try:
            card_data = _build_agent_card_payload(app_state, identity, host_base)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                A2A_AGENT_CARD_SERVED,
                card_type="agent",
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="agent_card_build_failed",
            )
            return _service_unavailable_response()

        await _put_cached_card(cache_key, card_data, ttl, fingerprint=fingerprint)
        logger.info(A2A_AGENT_CARD_SERVED, card_type="agent", agent_id=agent_id)
        return _card_response(card_data, ttl)
