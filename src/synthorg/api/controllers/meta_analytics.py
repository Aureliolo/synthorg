"""Cross-deployment analytics API controller.

Provides endpoints for event ingestion (collector role),
pattern querying, and threshold recommendations.
"""

from typing import Annotated, Final

from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.meta.telemetry.collector import InMemoryAnalyticsCollector
from synthorg.meta.telemetry.models import (
    AggregatedPattern,
    EventBatch,
    ThresholdRecommendation,
)
from synthorg.meta.telemetry.recommender import (
    DefaultThresholdRecommender,
)
from synthorg.observability import get_logger

logger = get_logger(__name__)
_DEFAULT_MIN_DEPLOYMENTS_FLOOR: Final[int] = 3
_DEFAULT_LIMIT: Final[int] = 50

# Module-level singleton instances.
# Created lazily by the app startup hook; None when collector is disabled.
_collector: InMemoryAnalyticsCollector | None = None
_recommender: DefaultThresholdRecommender | None = None
_min_deployments_floor: int = 3


def configure_analytics_controller(
    collector: InMemoryAnalyticsCollector | None,
    recommender: DefaultThresholdRecommender | None,
    *,
    min_deployments_floor: int = _DEFAULT_MIN_DEPLOYMENTS_FLOOR,
) -> None:
    """Configure the analytics controller with collector and recommender.

    Called during app startup when the collector role is enabled.

    Args:
        collector: Collector instance, or None if disabled.
        recommender: Recommender instance, or None if disabled.
        min_deployments_floor: Minimum deployments for pattern queries
            (from ``CrossDeploymentAnalyticsConfig.min_deployments_for_pattern``).
    """
    global _collector, _recommender, _min_deployments_floor  # noqa: PLW0603
    _collector = collector
    _recommender = recommender
    _min_deployments_floor = min_deployments_floor


def is_analytics_collector_configured() -> bool:
    """Return whether the collector role has already been configured.

    Lets the startup wiring hook stay idempotent: a second lifespan
    (e.g. the ``--count=2`` isolation gate) must not overwrite the live
    in-memory collector with a fresh empty instance and lose its events.

    Returns:
        ``True`` when a collector instance is already installed.
    """
    return _collector is not None


def _require_collector() -> InMemoryAnalyticsCollector:
    """Get the collector or raise ServiceUnavailableError.

    Returns:
        ``InMemoryAnalyticsCollector`` instance.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    if _collector is None:
        msg = "Cross-deployment analytics collector is not enabled"
        raise ServiceUnavailableError(msg)
    return _collector


class MetaAnalyticsController(Controller):
    """Cross-deployment analytics API endpoints.

    Provides event ingestion for the collector role and
    pattern/recommendation queries.
    """

    path = "/meta/analytics"
    tags = ["meta-analytics"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @post(
        "/events",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("meta.ingest_events", key="user"),
        ],
    )
    async def ingest_events(
        self,
        data: EventBatch,
    ) -> ApiResponse[dict[str, int]]:
        """Ingest a batch of anonymized outcome events.

        Only available when ``collector_enabled=True``.
        Requires write access.

        API-only: this is a backend-to-backend ingestion path used by
        reporting deployments; it has no dashboard surface (the dashboard
        only reads aggregated patterns and recommendations).

        Args:
            data: Batch of anonymized events.

        Returns:
            Number of events ingested.
        """
        collector = _require_collector()
        count = await collector.ingest(data.events)
        return ApiResponse[dict[str, int]](
            data={"ingested": count},
        )

    @get("/patterns")
    async def get_patterns(
        self,
        state: State,
        min_deployments: Annotated[
            int,
            QueryParameter(
                ge=1,
                le=100,
                description="Minimum deployment count for a pattern to be returned.",
            ),
        ] = _DEFAULT_MIN_DEPLOYMENTS_FLOOR,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[AggregatedPattern]:
        """Query aggregated cross-deployment patterns (paginated).

        Args:
            state: Application state.
            min_deployments: Minimum unique deployments for pattern.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            ``PaginatedResponse[AggregatedPattern]`` instance.
        """
        collector = _require_collector()
        # Clamp to configured privacy floor so callers cannot
        # request patterns below the deployment-count minimum.
        effective = max(min_deployments, _min_deployments_floor)
        patterns = await collector.query_patterns(
            min_deployments=effective,
        )
        page, meta = paginate_cursor(
            tuple(patterns),
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[AggregatedPattern](data=page, pagination=meta)

    @get("/recommendations")
    async def get_recommendations(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[ThresholdRecommendation]:
        """Get threshold recommendations from aggregated data (paginated).

        Returns:
            Paginated page of ``ThresholdRecommendation`` (items + cursor envelope).
        """
        collector = _require_collector()
        if _recommender is None:
            empty_recs: tuple[ThresholdRecommendation, ...] = ()
            page, meta = paginate_cursor(
                empty_recs,
                limit=limit,
                cursor=cursor,
                secret=cursor_secret_of(state.app_state),
            )
            return PaginatedResponse[ThresholdRecommendation](
                data=page,
                pagination=meta,
            )
        recs = await _recommender.get_recommendations(
            collector=collector,
        )
        page, meta = paginate_cursor(
            tuple(recs),
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[ThresholdRecommendation](
            data=page,
            pagination=meta,
        )
