"""API event constants."""

from typing import Final

API_REQUEST_STARTED: Final[str] = "api.request.started"
API_REQUEST_COMPLETED: Final[str] = "api.request.completed"
API_REQUEST_ERROR: Final[str] = "api.request.error"
API_HEALTH_CHECK: Final[str] = "api.health.check"
API_APP_STARTUP: Final[str] = "api.app.startup"
API_APP_SHUTDOWN: Final[str] = "api.app.shutdown"
API_WS_CONNECTED: Final[str] = "api.ws.connected"
API_WS_DISCONNECTED: Final[str] = "api.ws.disconnected"
API_GUARD_DENIED: Final[str] = "api.guard.denied"
