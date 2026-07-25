# module-kind: code
"""HTTP probe request for the provider health prober.

Split out of ``health_prober.py`` to keep that orchestrator under its
module-size budget. Owns the single outbound liveness GET (including the
429/rate-limit verdict) and returns a plain ``(elapsed_ms, success,
error)`` tuple so the orchestrator stays free of httpx detail.
"""

import asyncio
from typing import Final

import httpx
from httpx import AsyncClient

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_HEALTH_PROBE_FAILED
from synthorg.providers.health_prober_helpers import truncate
from synthorg.tools.network_validator import DnsValidationOk
from synthorg.tools.ssrf import build_pinned_transport

logger = get_logger(__name__)

_PROBE_TIMEOUT_SECONDS: Final[float] = 10.0
_HTTP_SERVER_ERROR_THRESHOLD: Final[int] = 500
_HTTP_TOO_MANY_REQUESTS: Final[int] = 429


async def execute_probe(
    url: str,
    headers: dict[str, str],
    *,
    clock: Clock,
    validation: DnsValidationOk | None = None,
) -> tuple[float, bool, str | None]:
    """Execute the HTTP probe request.

    Args:
        url: URL to probe.
        headers: Auth headers for the request.
        clock: Injected time source for the latency measurement.
        validation: DNS pre-flight result; when it carries resolved IPs the
            probe connects through a pinned transport so a DNS rebind cannot
            redirect it after the allowlist check.

    Returns:
        Tuple of (elapsed_ms, success, error_message). A 429 is NOT success
        (rate-limited != healthy) and surfaces the ``retry-after`` hint.

    Raises:
        asyncio.CancelledError: Re-raised if the task is cancelled during
            the probe.
    """
    start = clock.monotonic()
    success = False
    error_msg: str | None = None
    # ``transport=None`` is httpx's default-transport sentinel, so a
    # literal-IP target (no IPs to pin) connects normally.
    transport = build_pinned_transport(validation) if validation is not None else None

    try:
        # Bound into this module's namespace (rather than reached through
        # ``httpx.``) so a test double replaces the name here alone. Patching
        # the attribute on the shared httpx module would hand every other
        # library in the process a mock client for the duration.
        async with AsyncClient(
            timeout=_PROBE_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
        ) as client:
            resp = await client.get(url, headers=headers)
            success = (
                resp.status_code < _HTTP_SERVER_ERROR_THRESHOLD
                and resp.status_code != _HTTP_TOO_MANY_REQUESTS
            )
            if not success:
                if resp.status_code == _HTTP_TOO_MANY_REQUESTS:
                    # Truncate the attacker-controllable header before
                    # embedding it so a crafted value cannot inject newlines
                    # or bloat the diagnostic / log line.
                    retry_after = truncate(resp.headers.get("retry-after") or "")
                    error_msg = f"HTTP 429 rate limited (retry-after={retry_after})"
                else:
                    error_msg = f"HTTP {resp.status_code}"
    except httpx.ConnectError as exc:
        error_msg = f"connect failed: {type(exc).__name__}"
    except httpx.TimeoutException:
        error_msg = "timeout"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # An unexpected error here (SSRF rejection, TLS failure, DNS error)
        # is a probe-layer crash distinct from the expected connect/timeout
        # "unhealthy" outcomes; log it so it is visible server-side rather
        # than only surfaced as the returned error string.
        error_msg = truncate(f"{type(exc).__name__}: {safe_error_description(exc)}")
        logger.warning(
            PROVIDER_HEALTH_PROBE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    elapsed_ms = (clock.monotonic() - start) * 1000
    return elapsed_ms, success, error_msg
