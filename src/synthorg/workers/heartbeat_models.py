"""Worker liveness heartbeat envelope.

Published at-most-once on a core-NATS subject
(``synthorg.workers.heartbeat.<worker_id>``) by each worker, observed
by the backend's heartbeat subscriber to drive the worker-liveness
gauge. Correctness of crash recovery does NOT depend on this: a
crashed worker's claim is redelivered by JetStream ``ack_wait``. The
heartbeat exists purely for operator visibility.
"""

from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001

HEARTBEAT_SUBJECT_PREFIX: Final[str] = "synthorg.workers.heartbeat"
"""Core-NATS subject root for worker liveness beats.

Full subject is ``synthorg.workers.heartbeat.<worker_id>``. Core
(non-JetStream) so a beat is at-most-once and never enters the
``WorkQueuePolicy`` task stream. Shared by the worker publisher and
the backend subscriber so the wire contract has one source of truth.
"""


class WorkerHeartbeat(BaseModel):
    """A single liveness beat from one worker.

    Attributes:
        worker_id: Emitting worker's identifier (also the subject leaf).
        emitted_at: Wall-clock time the beat was produced (clock seam,
            so tests can assert cadence deterministically).
        claims_done: Count of claims this worker has carried to a
            terminal outcome since process start. Monotonic; lets an
            operator see a worker is not just alive but making progress.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    worker_id: NotBlankStr = Field(description="Emitting worker identifier")
    emitted_at: AwareDatetime = Field(description="When the beat was produced")
    claims_done: int = Field(
        default=0,
        ge=0,
        description="Terminal claims handled since worker start",
    )
