"""Governed discovery client for fetching remote A2A agent cards.

Fetches a peer's ``/.well-known/agent-card.json`` through the same
SSRF-pinned HTTP path the outbound :class:`~synthorg.a2a.client.A2AClient`
uses, validates the payload as an :class:`~synthorg.a2a.models.A2AAgentCard`,
and registers it in the :class:`~synthorg.a2a.peer_registry.PeerRegistry` so
the gateway's ``skills/query`` / ``skills/negotiate`` methods can route to
the peer. Card endpoints are unauthenticated by design (public per the A2A
spec), so no connection-catalog credentials are pulled.
"""

from typing import Final, NoReturn

import httpx

from synthorg.a2a.client import A2AClientError, A2ATransientError
from synthorg.a2a.models import A2AAgentCard
from synthorg.a2a.peer_registry import PeerRegistry
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import strip_trailing_slash
from synthorg.core.resilience.retry_after import (
    coerce_finite_nonneg_seconds,
    parse_retry_after_seconds,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.a2a import (
    A2A_OUTBOUND_SSRF_BLOCKED,
    A2A_PEER_DISCOVERED,
    A2A_PEER_DISCOVERY_FAILED,
)
from synthorg.tools.network_validator import NetworkPolicy

logger = get_logger(__name__)

_WELL_KNOWN_CARD_PATH: Final[str] = "/.well-known/agent-card.json"
_HTTP_OK: Final[int] = 200
_HTTP_TOO_MANY_REQUESTS: Final[int] = 429


class PeerDiscoveryClient:
    """Fetch + validate + register a remote peer's Agent Card.

    The SSRF validation pins the connection to the validator-approved IP so a
    DNS rebind between the check and the connect cannot redirect the fetch to a
    private address (TOCTOU); the response is streamed and the running byte
    count is enforced against ``max_card_bytes`` as it arrives, so a hostile
    peer cannot exhaust memory by returning an oversized card body.

    Args:
        peer_registry: Registry to commit discovered cards into.
        timeout_seconds: HTTP request timeout. Threaded from the wiring site so
            the constructor carries no hidden default (pre-alpha discipline).
        max_card_bytes: Hard cap on the card response body size.
        network_validator: SSRF policy; ``None`` skips pinning (test-only).
        http_client: Optional injected client for tests (used as-is, no pin).
    """

    __slots__ = (
        "_http_client",
        "_max_card_bytes",
        "_network_validator",
        "_registry",
        "_timeout",
    )

    def __init__(
        self,
        *,
        peer_registry: PeerRegistry,
        timeout_seconds: float,
        max_card_bytes: int,
        network_validator: NetworkPolicy | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._registry = peer_registry
        self._timeout = timeout_seconds
        self._max_card_bytes = max_card_bytes
        self._network_validator = network_validator
        self._http_client = http_client

    async def discover(self, peer_name: str, base_url: str) -> A2AAgentCard:
        """Fetch, validate, and register a peer's Agent Card.

        Args:
            peer_name: Connection name to register the discovered card under.
            base_url: Peer base URL; ``/.well-known/agent-card.json`` is
                appended to locate the card.

        Returns:
            The validated, registered :class:`A2AAgentCard`.

        Raises:
            A2ATransientError: When the fetch times out or the connection
                resets (the caller may retry).
            A2AClientError: When the URL is SSRF-blocked, the fetch returns
                non-200, the body exceeds ``max_card_bytes``, or the payload
                is not a valid Agent Card.
        """
        pinned_ip, pinned_hostname = await self._validate_and_pin(peer_name, base_url)
        card_url = f"{strip_trailing_slash(base_url)}{_WELL_KNOWN_CARD_PATH}"
        body = await self._fetch(peer_name, card_url, pinned_ip, pinned_hostname)
        card = self._parse(peer_name, body)
        await self._registry.register(peer_name, card)
        logger.info(
            A2A_PEER_DISCOVERED,
            peer_name=peer_name,
            skill_count=len(card.skills),
        )
        return card

    async def _validate_and_pin(
        self,
        peer_name: str,
        base_url: str,
    ) -> tuple[str | None, str | None]:
        """Resolve + SSRF-validate the base URL, returning the pin pair.

        Returns:
            ``(pinned_ip, pinned_hostname)`` when a validator is configured and
            DNS resolved, else ``(None, None)`` (test path / no validator).

        Raises:
            A2AClientError: When the URL is unparseable or SSRF-blocked.
        """
        if self._network_validator is None:
            return None, None
        from synthorg.tools.network_validator import (  # noqa: PLC0415
            extract_hostname,
            validate_url_host,
        )

        if extract_hostname(base_url) is None:
            self._raise_ssrf_blocked(peer_name, base_url, reason="unparseable URL")
        try:
            validation = await validate_url_host(base_url, self._network_validator)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._raise_ssrf_blocked(peer_name, base_url, exc=exc)
        if isinstance(validation, str):
            self._raise_ssrf_blocked(peer_name, base_url, reason=validation)
        if validation.resolved_ips:
            return validation.resolved_ips[0], validation.hostname
        return None, None

    def _raise_ssrf_blocked(
        self,
        peer_name: str,
        base_url: str,
        *,
        reason: str | None = None,
        exc: Exception | None = None,
    ) -> NoReturn:
        """Log the SSRF block and raise, redacting any underlying error.

        Raises:
            A2AClientError: Always; this never returns.
        """
        if exc is not None:
            logger.warning(
                A2A_OUTBOUND_SSRF_BLOCKED,
                peer_name=peer_name,
                url=base_url,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            logger.warning(
                A2A_OUTBOUND_SSRF_BLOCKED,
                peer_name=peer_name,
                url=base_url,
                reason=reason,
            )
        msg = f"SSRF: blocked discovery URL for peer '{peer_name}'"
        raise A2AClientError(msg, peer_name=peer_name) from exc

    async def _fetch(
        self,
        peer_name: str,
        card_url: str,
        pinned_ip: str | None,
        pinned_hostname: str | None,
    ) -> bytes:
        """Stream the card URL through the pinned transport, byte-bounded.

        Returns:
            The card response body, capped at ``max_card_bytes``.

        Raises:
            A2ATransientError: On a connection/timeout error the caller may
                retry (the peer was momentarily unreachable).
            A2AClientError: On a non-transient transport error, a non-200
                status, or a body exceeding the size cap.
        """
        try:
            if self._http_client is not None:
                return await self._stream_bounded(
                    self._http_client, card_url, peer_name
                )
            transport: httpx.AsyncBaseTransport | None = None
            if pinned_ip is not None and pinned_hostname is not None:
                from synthorg.tools._dns_pinning import (  # noqa: PLC0415
                    PinnedDnsTransport,
                )

                transport = PinnedDnsTransport(hostname=pinned_hostname, ip=pinned_ip)
            # ``AsyncClient`` owns and closes the injected transport on exit;
            # an explicit aclose() would double-close it.
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=transport
            ) as http:
                return await self._stream_bounded(http, card_url, peer_name)
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            logger.warning(
                A2A_PEER_DISCOVERY_FAILED,
                peer_name=peer_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Agent-card fetch for peer '{peer_name}' timed out or reset"
            raise A2ATransientError(msg, peer_name=peer_name) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                A2A_PEER_DISCOVERY_FAILED,
                peer_name=peer_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Agent-card fetch for peer '{peer_name}' failed"
            raise A2AClientError(msg, peer_name=peer_name) from exc

    async def _stream_bounded(
        self,
        http: httpx.AsyncClient,
        card_url: str,
        peer_name: str,
    ) -> bytes:
        """Stream a GET, enforcing the byte cap as chunks arrive.

        Returns:
            The body bytes, guaranteed <= ``max_card_bytes``.

        Raises:
            A2ATransientError: On a 429 (retryable by the caller).
            A2AClientError: On any other non-200 status or a body over the cap.
        """
        async with http.stream("GET", card_url) as response:
            if response.status_code == _HTTP_TOO_MANY_REQUESTS:
                # Rate-limited card fetch is transient: surface it as
                # retryable so a caller re-discovers once the peer recovers,
                # mirroring the outbound transport's 429 handling.
                retry_after = coerce_finite_nonneg_seconds(
                    parse_retry_after_seconds(response.headers.get("Retry-After")),
                )
                logger.warning(
                    A2A_PEER_DISCOVERY_FAILED,
                    peer_name=peer_name,
                    status=response.status_code,
                    reason="rate_limited",
                )
                msg = f"Peer '{peer_name}' agent card fetch rate-limited (429)"
                raise A2ATransientError(
                    msg,
                    peer_name=peer_name,
                    retry_after_seconds=retry_after,
                )
            if response.status_code != _HTTP_OK:
                logger.warning(
                    A2A_PEER_DISCOVERY_FAILED,
                    peer_name=peer_name,
                    status=response.status_code,
                )
                msg = f"Peer '{peer_name}' agent card returned {response.status_code}"
                raise A2AClientError(msg, peer_name=peer_name)
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self._max_card_bytes:
                    logger.warning(
                        A2A_PEER_DISCOVERY_FAILED,
                        peer_name=peer_name,
                        reason="card_too_large",
                        byte_count=len(body),
                    )
                    msg = f"Peer '{peer_name}' agent card exceeds the size cap"
                    raise A2AClientError(msg, peer_name=peer_name)
            return bytes(body)

    def _parse(self, peer_name: str, body: bytes) -> A2AAgentCard:
        """Validate the (already byte-bounded) body into an Agent Card.

        Returns:
            The validated :class:`A2AAgentCard`.

        Raises:
            A2AClientError: When the payload is not a valid Agent Card.
        """
        try:
            return A2AAgentCard.model_validate_json(body)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                A2A_PEER_DISCOVERY_FAILED,
                peer_name=peer_name,
                reason="invalid_card",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Peer '{peer_name}' returned an invalid agent card"
            raise A2AClientError(msg, peer_name=peer_name) from exc


__all__ = ["PeerDiscoveryClient"]
