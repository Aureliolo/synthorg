"""Docker sandbox event constants."""

from typing import Final

DOCKER_EXECUTE_START: Final[str] = "docker.execute.start"
DOCKER_EXECUTE_SUCCESS: Final[str] = "docker.execute.success"
DOCKER_EXECUTE_FAILED: Final[str] = "docker.execute.failed"
DOCKER_EXECUTE_TIMEOUT: Final[str] = "docker.execute.timeout"
DOCKER_CONTAINER_CREATED: Final[str] = "docker.container.created"
DOCKER_CONTAINER_STOPPED: Final[str] = "docker.container.stopped"
DOCKER_CONTAINER_REMOVED: Final[str] = "docker.container.removed"
DOCKER_CONTAINER_STOP_FAILED: Final[str] = "docker.container.stop_failed"
DOCKER_CONTAINER_REMOVE_FAILED: Final[str] = "docker.container.remove_failed"
DOCKER_CLEANUP: Final[str] = "docker.cleanup"
DOCKER_HEALTH_CHECK: Final[str] = "docker.health_check"
DOCKER_DAEMON_UNAVAILABLE: Final[str] = "docker.daemon.unavailable"
