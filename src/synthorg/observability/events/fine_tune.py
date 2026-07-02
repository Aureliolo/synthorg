"""Fine-tune pipeline runtime event constants.

Emitted by the ephemeral stage-container launcher
(``synthorg.memory.embedding.fine_tune_docker_runner``) and the
preflight probe. Separated from ``events.memory`` so the container
lifecycle is queryable independently of the memory subsystem.
"""

from typing import Final

FINE_TUNE_CONTAINER_STARTED: Final[str] = "fine_tune.container.started"
FINE_TUNE_CONTAINER_COMPLETED: Final[str] = "fine_tune.container.completed"
FINE_TUNE_CONTAINER_FAILED: Final[str] = "fine_tune.container.failed"
FINE_TUNE_CONTAINER_CANCELLED: Final[str] = "fine_tune.container.cancelled"
FINE_TUNE_CONTAINER_TIMED_OUT: Final[str] = "fine_tune.container.timed_out"
FINE_TUNE_PROBE_STARTED: Final[str] = "fine_tune.probe.started"
FINE_TUNE_PROBE_OK: Final[str] = "fine_tune.probe.ok"
FINE_TUNE_PROBE_FAILED: Final[str] = "fine_tune.probe.failed"
