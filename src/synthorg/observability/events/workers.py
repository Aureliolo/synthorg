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
WORKERS_DEDUP_MARK_FAILED: Final[str] = "workers.worker.dedup_mark_failed"

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
WORKERS_QUEUE_START_REJECTED: Final[str] = "workers.task_queue.start_rejected"
WORKERS_QUEUE_NOT_RUNNING: Final[str] = "workers.task_queue.not_running"
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
WORKERS_MAIN_INVALID_EXECUTOR_CONFIG: Final[str] = (
    "workers.main.invalid_executor_config"
)
WORKERS_MAIN_PLACEHOLDER_EXECUTOR_INVOKED: Final[str] = (
    "workers.main.placeholder_executor_invoked"
)

# HTTP-callback executor events
WORKERS_EXECUTOR_HTTP_INVOKED: Final[str] = "workers.executor.http_invoked"
WORKERS_EXECUTOR_HTTP_TERMINAL: Final[str] = "workers.executor.http_terminal"
WORKERS_EXECUTOR_HTTP_RETRY: Final[str] = "workers.executor.http_retry"
WORKERS_EXECUTOR_HTTP_FAILED: Final[str] = "workers.executor.http_failed"
WORKERS_EXECUTOR_INVALID_INIT_ARG: Final[str] = "workers.executor.invalid_init_arg"

# Backend-side execution service events
WORKERS_EXECUTION_SERVICE_ATTEMPTED: Final[str] = "workers.execution_service.attempted"
WORKERS_EXECUTION_SERVICE_COMPLETED: Final[str] = "workers.execution_service.completed"
WORKERS_EXECUTION_SERVICE_NO_OP: Final[str] = "workers.execution_service.no_op"
WORKERS_EXECUTION_SERVICE_TASK_NOT_FOUND: Final[str] = (
    "workers.execution_service.task_not_found"
)
WORKERS_EXECUTION_SERVICE_AGENT_RUN: Final[str] = "workers.execution_service.agent_run"
WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED: Final[str] = (
    "workers.execution_service.autonomy_degraded"
)
WORKERS_EXECUTION_SERVICE_NO_PROVIDER: Final[str] = (
    "workers.execution_service.no_provider"
)
WORKERS_EXECUTION_SERVICE_FAILED: Final[str] = "workers.execution_service.failed"
WORKERS_EXECUTION_SERVICE_SANDBOX_RELEASED: Final[str] = (
    "workers.execution_service.sandbox_released"
)
WORKERS_EXECUTION_SERVICE_SANDBOX_RELEASE_FAILED: Final[str] = (
    "workers.execution_service.sandbox_release_failed"
)
