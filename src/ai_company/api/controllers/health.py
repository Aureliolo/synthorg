"""Health check controller."""

import time

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from ai_company import __version__
from ai_company.api.dto import ApiResponse
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.api import API_HEALTH_CHECK

logger = get_logger(__name__)

_STARTUP_TIME: float = time.monotonic()


class HealthStatus(BaseModel):
    """Health check response payload.

    Attributes:
        status: Overall health status.
        persistence: Whether persistence backend is healthy.
        message_bus: Whether message bus is running.
        version: Application version.
        uptime_seconds: Seconds since application startup.
    """

    model_config = ConfigDict(frozen=True)

    status: str = Field(description="Overall health status")
    persistence: bool = Field(
        description="Persistence backend healthy",
    )
    message_bus: bool = Field(
        description="Message bus running",
    )
    version: str = Field(description="Application version")
    uptime_seconds: float = Field(
        description="Seconds since startup",
    )


class HealthController(Controller):
    """Health check endpoint."""

    path = "/health"
    tags = ("health",)

    @get()
    async def health_check(
        self,
        state: State,
    ) -> ApiResponse[HealthStatus]:
        """Return current health status.

        Args:
            state: Application state.

        Returns:
            Health status envelope.
        """
        app_state: AppState = state.app_state
        persistence_ok = await app_state.persistence.health_check()
        bus_ok = app_state.message_bus.is_running

        if persistence_ok and bus_ok:
            status = "ok"
        elif persistence_ok or bus_ok:
            status = "degraded"
        else:
            status = "down"

        uptime = round(time.monotonic() - _STARTUP_TIME, 2)

        logger.debug(
            API_HEALTH_CHECK,
            status=status,
            persistence=persistence_ok,
            message_bus=bus_ok,
        )

        return ApiResponse(
            data=HealthStatus(
                status=status,
                persistence=persistence_ok,
                message_bus=bus_ok,
                version=__version__,
                uptime_seconds=uptime,
            ),
        )
