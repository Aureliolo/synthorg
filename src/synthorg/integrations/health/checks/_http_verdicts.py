"""Turning an HTTP probe outcome into a health verdict.

Separate from issuing the probe: what a response *means* is a per-vendor
judgement with its own rules (an expected rejection proves a credential
cleared; a rate limit proves nothing either way), while issuing it is
transport mechanics.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, NamedTuple

import httpx

from synthorg.integrations.connections.http_vendor import (
    HttpVendorPreset,
    ProbeVerdict,
)
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    HealthReport,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_CHECK_PASSED,
)

logger = get_logger(__name__)

ERROR_THRESHOLD: Final[int] = 400
TOO_MANY_REQUESTS: Final[int] = 429
UNAUTHORIZED: Final[int] = 401
FORBIDDEN: Final[int] = 403


def _retry_after_seconds(raw: str) -> float | None:
    """Read a ``Retry-After`` delay, ignoring the HTTP-date form.

    Only the delta-seconds form is honoured. The date form would need the
    server's clock to agree with ours to mean anything, and a probe deferred
    on a wrong clock is worse than one deferred on the default interval.

    Returns:
        The delay in seconds, or ``None`` when the header says nothing usable.
    """
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


class ProbeResponse(NamedTuple):
    """A probe response reduced to what a verdict needs.

    The body is bounded at the point it is read rather than handed over as a
    live :class:`httpx.Response`. A ``generic_http`` connection points
    wherever the operator says, the prober re-reads every one of them on a
    loop, and the verdict logic needs at most an error message, so buffering
    an arbitrarily large body into the API process would be a standing
    resource-exhaustion surface for no gain.
    """

    status_code: int
    text: str
    headers: Mapping[str, str]


def _vendor_error_report(
    connection: Connection,
    resp: ProbeResponse,
    elapsed: float,
    preset: HttpVendorPreset,
) -> HealthReport | None:
    """Judge a vendor's error response against its own contract.

    The probe deliberately sends a request the endpoint must reject, so for a
    vendor-bound connection an error is the expected outcome and the question
    is only which kind. An authentication rejection is a real fault; a
    rejection of the request shape proves the credential cleared.

    Args:
        connection: The connection being probed.
        resp: The probe's response.
        elapsed: Probe latency in milliseconds.
        preset: The vendor contract to read the response through.

    Returns:
        The verdict when the vendor's contract settles it, or ``None`` to
        fall through to the generic status-based judgement.
    """
    # A flat auth rejection and a rate limit are already unambiguous, and the
    # generic path reports each with its own cause (including the retry hint).
    # Reading them through the vendor's error contract would relabel a
    # throttled connection as merely unverifiable.
    if resp.status_code in (UNAUTHORIZED, FORBIDDEN, TOO_MANY_REQUESTS):
        return None
    verdict = preset.probe_verdict(resp.status_code, resp.text)
    if verdict is ProbeVerdict.AUTH_FAILED:
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=connection.name,
            status_code=resp.status_code,
            reason="credential_rejected",
        )
        return HealthReport(
            connection_name=connection.name,
            status=ConnectionStatus.UNHEALTHY,
            latency_ms=elapsed,
            error_detail="credential rejected by the provider",
            checked_at=datetime.now(UTC),
        )
    if verdict is ProbeVerdict.AUTH_OK:
        logger.info(
            HEALTH_CHECK_PASSED,
            connection_name=connection.name,
            latency_ms=elapsed,
            probe="unbilled",
        )
        return HealthReport(
            connection_name=connection.name,
            status=ConnectionStatus.HEALTHY,
            latency_ms=elapsed,
            checked_at=datetime.now(UTC),
        )
    # Unknown, not unhealthy: this vendor has published no way to prove a
    # credential without buying a request, so the honest report is that the
    # probe could not tell. Calling it unhealthy would cry wolf on a working
    # connection; calling it healthy would hide a revoked key.
    #
    # Logged like its siblings so a connection sitting at Unknown for days is
    # greppable: the report reaches the dashboard, but without an event there
    # is nothing to search when someone asks why.
    logger.info(
        HEALTH_CHECK_PASSED,
        connection_name=connection.name,
        status_code=resp.status_code,
        verdict="indeterminate",
    )
    return HealthReport(
        connection_name=connection.name,
        status=ConnectionStatus.UNKNOWN,
        latency_ms=elapsed,
        error_detail=(
            f"HTTP {resp.status_code}: no unbilled way to verify this "
            "provider's credential, so its state is unconfirmed"
        ),
        checked_at=datetime.now(UTC),
    )


def report_response(
    connection: Connection,
    resp: ProbeResponse,
    elapsed: float,
    preset: HttpVendorPreset | None = None,
) -> HealthReport:
    """Turn a probe response into a health verdict.

    Args:
        connection: The connection being probed.
        resp: The probe's response.
        elapsed: Probe latency in milliseconds.
        preset: The vendor contract, when the connection is vendor-bound.

    Returns:
        ``HEALTHY`` below the error threshold, else the vendor's own verdict
        on its error, else ``UNHEALTHY`` carrying the status and, for a rate
        limit, its retry hint.
    """
    if resp.status_code >= ERROR_THRESHOLD and preset is not None:
        vendor_verdict = _vendor_error_report(connection, resp, elapsed, preset)
        if vendor_verdict is not None:
            return vendor_verdict
    if resp.status_code < ERROR_THRESHOLD:
        logger.info(
            HEALTH_CHECK_PASSED,
            connection_name=connection.name,
            latency_ms=elapsed,
        )
        return HealthReport(
            connection_name=connection.name,
            status=ConnectionStatus.HEALTHY,
            latency_ms=elapsed,
            checked_at=datetime.now(UTC),
        )
    # A rate limit says nothing about whether the credential is valid, so it
    # is reported as its own cause rather than folded into the generic
    # failure detail an operator would read as one.
    retry_after = resp.headers.get("Retry-After") or ""
    rate_limited = resp.status_code == TOO_MANY_REQUESTS
    logger.warning(
        HEALTH_CHECK_FAILED,
        connection_name=connection.name,
        status_code=resp.status_code,
        reason="rate_limited" if rate_limited else "http_error",
        retry_after=retry_after,
    )
    detail = f"HTTP {resp.status_code}"
    if rate_limited and retry_after:
        detail = f"{detail} (retry after {retry_after})"
    return HealthReport(
        connection_name=connection.name,
        status=ConnectionStatus.UNHEALTHY,
        retry_after_seconds=_retry_after_seconds(retry_after) if rate_limited else None,
        latency_ms=elapsed,
        error_detail=detail,
        checked_at=datetime.now(UTC),
    )


def deadline_report(
    connection: Connection,
    elapsed: float,
    timeout_seconds: float,
) -> HealthReport:
    """Turn a probe that outlived its deadline into a health verdict.

    Args:
        connection: The connection being probed.
        elapsed: Probe latency in milliseconds.
        timeout_seconds: The deadline the probe breached.

    Returns:
        ``UNHEALTHY`` naming the deadline the probe breached.
    """
    logger.warning(
        HEALTH_CHECK_FAILED,
        connection_name=connection.name,
        reason="probe_deadline_exceeded",
        timeout_seconds=timeout_seconds,
    )
    return HealthReport(
        connection_name=connection.name,
        status=ConnectionStatus.UNHEALTHY,
        latency_ms=elapsed,
        error_detail=f"probe exceeded {timeout_seconds}s",
        checked_at=datetime.now(UTC),
    )


def network_report(
    connection: Connection,
    exc: httpx.HTTPError,
    elapsed: float,
) -> HealthReport:
    """Turn a probe that failed below HTTP into a health verdict.

    Args:
        connection: The connection being probed.
        exc: The transport failure.
        elapsed: Probe latency in milliseconds.

    Returns:
        ``UNHEALTHY`` carrying the scrubbed transport error.
    """
    scrubbed = safe_error_description(exc)
    logger.warning(
        HEALTH_CHECK_FAILED,
        connection_name=connection.name,
        error_type=type(exc).__name__,
        error=scrubbed,
    )
    return HealthReport(
        connection_name=connection.name,
        status=ConnectionStatus.UNHEALTHY,
        latency_ms=elapsed,
        error_detail=scrubbed,
        checked_at=datetime.now(UTC),
    )
