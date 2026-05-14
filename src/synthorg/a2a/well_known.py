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
        a2a_config = app_state.config.a2a
        ttl = a2a_config.agent_card_cache_ttl_seconds

        host_base = strip_trailing_slash(str(request.base_url))
        company_cache_key = f"__company__:{host_base}"
        # Fingerprint not checked on read for company card (requires
        # listing all agents); TTL-based expiry is the primary guard.
        cached = await _get_cached_card(company_cache_key, ttl)
        if cached is not None:
            logger.debug(
                A2A_AGENT_CARD_CACHE_HIT,
                cache_key=company_cache_key,
            )
            return Response(
                content=cached,
                media_type="application/json",
                headers={
                    "Cache-Control": f"public, max-age={ttl}",
                },
            )

        logger.debug(
            A2A_AGENT_CARD_CACHE_MISS,
            cache_key=company_cache_key,
        )

        builder: AgentCardBuilder = app_state.a2a_card_builder
        registry = app_state.agent_registry

        try:
            identities = await registry.list_active()
            base_url = strip_trailing_slash(str(request.base_url))
            company_name = await _resolve_company_name(app_state)
            card = builder.build_company_card(
                identities=identities,
                base_url=f"{base_url}/api/v1/a2a",
                company_name=company_name,
            )
            card_data = card.model_dump()
            # Fingerprint: sorted identity IDs for staleness detection.
            id_fp = hashlib.sha256(
                ",".join(
                    sorted(str(i.id) for i in identities),
                ).encode(),
            ).hexdigest()[:16]
            cache_key = f"__company__:{base_url}"
            await _put_cached_card(
                cache_key,
                card_data,
                ttl,
                fingerprint=id_fp,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                A2A_AGENT_CARD_SERVED,
                card_type="company",
                error="Failed to build company agent card",
            )
            return Response(
                content={"error": "Service temporarily unavailable"},
                media_type="application/json",
                status_code=503,
            )

        logger.info(
            A2A_AGENT_CARD_SERVED,
            card_type="company",
            agent_count=len(identities),
        )
        return Response(
            content=card_data,
            media_type="application/json",
            headers={
                "Cache-Control": f"public, max-age={ttl}",
            },
        )

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
        a2a_config = app_state.config.a2a
        ttl = a2a_config.agent_card_cache_ttl_seconds

        host_base = strip_trailing_slash(str(request.base_url))
        agent_cache_key = f"{agent_id}:{host_base}"
        cached = await _get_cached_card(agent_cache_key, ttl)
        if cached is not None:
            logger.debug(
                A2A_AGENT_CARD_CACHE_HIT,
                cache_key=agent_cache_key,
            )
            return Response(
                content=cached,
                media_type="application/json",
                headers={
                    "Cache-Control": f"public, max-age={ttl}",
                },
            )

        logger.debug(
            A2A_AGENT_CARD_CACHE_MISS,
            cache_key=agent_cache_key,
        )

        registry = app_state.agent_registry

        try:
            identity = await registry.get(agent_id)
            if identity is None:
                identity = await registry.get_by_name(agent_id)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                A2A_AGENT_CARD_SERVED,
                card_type="agent",
                agent_id=agent_id,
                error="Failed to build agent card",
            )
            return Response(
                content={"error": "Service temporarily unavailable"},
                media_type="application/json",
                status_code=503,
            )

        if identity is None:
            msg = f"Agent '{agent_id}' not found"
            raise NotFoundError(msg)

        try:
            builder: AgentCardBuilder = app_state.a2a_card_builder
            card = builder.build(
                identity=identity,
                base_url=f"{host_base}/api/v1/a2a",
            )
            card_data = card.model_dump()
            # Fingerprint: identity name + role + skills for staleness.
            agent_fp = hashlib.sha256(
                f"{identity.name}:{identity.role}:{identity.skills}".encode(),
            ).hexdigest()[:16]
            await _put_cached_card(
                agent_cache_key,
                card_data,
                ttl,
                fingerprint=agent_fp,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                A2A_AGENT_CARD_SERVED,
                card_type="agent",
                agent_id=agent_id,
                error="Failed to build agent card",
            )
            return Response(
                content={"error": "Service temporarily unavailable"},
                media_type="application/json",
                status_code=503,
            )

        logger.info(
            A2A_AGENT_CARD_SERVED,
            card_type="agent",
            agent_id=agent_id,
        )
        return Response(
            content=card_data,
            media_type="application/json",
            headers={
                "Cache-Control": f"public, max-age={ttl}",
            },
        )
