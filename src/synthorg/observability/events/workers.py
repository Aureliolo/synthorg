"""Distributed task queue worker event constants.

Separate domain under ``synthorg.observability.events`` for the
``synthorg.workers`` package (see Distributed Runtime design). Keeps
event naming consistent with the other domain modules
(``communication``, ``task_engine``, ...).
"""

from typing import Final

# Worker lifecycle
WORKERS_WORKER_STARTED: Final[str] = "workers.worker.started"
WORKERS_WORKER_STOPPED: Final[str] = "workers.worker.stopped"
WORKERS_POOL_STARTED: Final[str] = "workers.pool.started"

# Claim execution
WORKERS_CLAIM_RECEIVED: Final[str] = "workers.worker.claim_received"
WORKERS_EXECUTOR_FAILED: Final[str] = "workers.worker.executor_failed"
WORKERS_FINALIZE_FAILED: Final[str] = "workers.worker.finalize_failed"
WORKERS_DUPLICATE_CLAIM_SUPPRESSED: Final[str] = (
    "workers.worker.duplicate_claim_suppressed"
)
WORKERS_DEDUP_LOOKUP_FAILED: Final[str] = "workers.worker.dedup_lookup_failed"
WORKERS_DEDUP_FORGET_FAILED: Final[str] = "workers.worker.dedup_forget_failed"

# Dispatcher
WORKERS_DISPATCHER_QUEUE_NOT_RUNNING: Final[str] = (
    "workers.dispatcher.queue_not_running"
)
WORKERS_DISPATCHER_PUBLISH_FAILED: Final[str] = "workers.dispatcher.publish_failed"
WORKERS_DISPATCHER_PUBLISH_RETRYING: Final[str] = "workers.dispatcher.publish_retrying"
WORKERS_DISPATCHER_PUBLISH_EXHAUSTED: Final[str] = (
    "workers.dispatcher.publish_exhausted"
)
WORKERS_DISPATCHER_CLAIM_ENQUEUED: Final[str] = "workers.dispatcher.claim_enqueued"

# Task queue client
WORKERS_TASK_QUEUE_CONNECT_FAILED: Final[str] = "workers.task_queue.connect_failed"
WORKERS_TASK_QUEUE_UNSUBSCRIBE_FAILED: Final[str] = (
    "workers.task_queue.unsubscribe_failed"
)
WORKERS_TASK_QUEUE_DRAIN_FAILED: Final[str] = "workers.task_queue.drain_failed"
WORKERS_TASK_QUEUE_ACK_MALFORMED_FAILED: Final[str] = (
    "workers.task_queue.ack_malformed_failed"
)
WORKERS_TASK_QUEUE_CLAIM_PARSE_FAILED: Final[str] = (
    "workers.task_queue.claim_parse_failed"
)

# Main entry point
WORKERS_MAIN_INVALID_WORKER_COUNT: Final[str] = "workers.main.invalid_worker_count"
WORKERS_MAIN_PLACEHOLDER_EXECUTOR_INVOKED: Final[str] = (
    "workers.main.placeholder_executor_invoked"
)
