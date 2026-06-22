"""Prometheus metrics scrape endpoint."""

import asyncio

from litestar import Controller, Response, get
from litestar.datastructures import State
from prometheus_client import generate_latest

from synthorg.api.rate_limits import (
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.metrics import (
    METRICS_SCRAPE_COMPLETED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.state import ObservabilityStateSlice

logger = get_logger(__name__)

_PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


class MetricsController(Controller):
    """Prometheus metrics scrape endpoint.

    Unauthenticated -- standard Prometheus scrape target.
    Follows the same pattern as ``HealthController``.

    API-only by design: this exposes the Prometheus exposition format
    for an external scraper (Prometheus / Grafana Agent). There is no
    in-app dashboard viewer; operators graph these metrics in their own
    observability stack, so the absence of a frontend surface is
    intentional, not a coverage gap.
    """

    path = "/metrics"
    tags = ("metrics",)

    @get(
        guards=[per_op_rate_limit_from_policy("observability.metrics", key="ip")],
        opt=per_op_concurrency_from_policy("observability.metrics", key="ip"),
    )
    async def metrics(self, state: State) -> Response[bytes]:
        """Refresh and return Prometheus metrics in exposition format.

        Args:
            state: Application state.

        Returns:
            Prometheus exposition format response.
        """
        app_state: AppState = state.app_state

        collector = app_state.slice(ObservabilityStateSlice).prometheus_collector
        # Intentional RFC 9457 bypass: the /metrics endpoint speaks the
        # Prometheus text exposition format, not the JSON problem-detail
        # envelope every other controller raises DomainError into. A
        # scraper expects a text/plain body (commented "# ..." lines) and
        # a bare status code, so the unwired-collector (503) and
        # scrape-failure (500) paths return a plain-text Response here
        # rather than raising ServiceUnavailableError/InternalError.
        if collector is None:
            logger.warning(METRICS_SCRAPE_FAILED, reason="collector not configured")
            return Response(
                content=b"# No metrics collector configured\n",
                media_type=_PROMETHEUS_CONTENT_TYPE,
                status_code=503,
            )

        try:
            await collector.refresh(app_state)
            # ``generate_latest`` serialises the whole registry
            # synchronously; offload it so a large registry does not
            # block the event loop during a scrape.
            body = await asyncio.to_thread(generate_latest, collector.registry)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                reason="refresh or generate_latest failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return Response(
                content=b"# Metrics scrape failed\n",
                media_type=_PROMETHEUS_CONTENT_TYPE,
                status_code=500,
            )

        logger.debug(METRICS_SCRAPE_COMPLETED, size_bytes=len(body))
        return Response(
            content=body,
            media_type=_PROMETHEUS_CONTENT_TYPE,
        )
