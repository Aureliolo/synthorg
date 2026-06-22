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
WORKERS_POOL_STOP_FAILED: Final[str] = "workers.pool.stop_failed"

# Claim execution
WORKERS_CLAIM_RECEIVED: Final[str] = "workers.worker.claim_received"
WORKERS_EXECUTOR_FAILED: Final[str] = "workers.worker.executor_failed"
WORKERS_FINALIZE_FAILED: Final[str] = "workers.worker.finalize_failed"
WORKERS_DUPLICATE_CLAIM_SUPPRESSED: Final[str] = (
    "workers.worker.duplicate_claim_suppressed"
)
WORKERS_DEDUP_LOOKUP_FAILED: Final[str] = "workers.worker.dedup_lookup_failed"
WORKERS_DEDUP_MARK_FAILED: Final[str] = "workers.worker.dedup_mark_failed"
WORKERS_DEDUP_BYPASS_PERMANENT: Final[str] = "workers.worker.dedup_bypass_permanent"
WORKERS_ACK_EXTEND_FAILED: Final[str] = "workers.worker.ack_extend_failed"
WORKERS_CLAIM_DEAD_LETTERED: Final[str] = "workers.worker.claim_dead_lettered"
WORKERS_DEAD_LETTER_PUBLISH_FAILED: Final[str] = (
    "workers.worker.dead_letter_publish_failed"
)

# Worker heartbeat (core-NATS liveness)
WORKERS_HEARTBEAT_SENT: Final[str] = "workers.worker.heartbeat_sent"
WORKERS_HEARTBEAT_FAILED: Final[str] = "workers.worker.heartbeat_failed"

# Dead-letter consumer (backend-side)
WORKERS_DEAD_LETTER_CONSUMER_STARTED: Final[str] = (
    "workers.dead_letter.consumer_started"
)
WORKERS_DEAD_LETTER_CONSUMER_STOPPED: Final[str] = (
    "workers.dead_letter.consumer_stopped"
)
WORKERS_DEAD_LETTER_TRANSITIONED: Final[str] = "workers.dead_letter.transitioned"
WORKERS_DEAD_LETTER_ALREADY_TERMINAL: Final[str] = (
    "workers.dead_letter.already_terminal"
)
WORKERS_DEAD_LETTER_DUPLICATE_SUPPRESSED: Final[str] = (
    "workers.dead_letter.duplicate_suppressed"
)
WORKERS_DEAD_LETTER_FAILED: Final[str] = "workers.dead_letter.failed"

# seen_claims pruner (backend-side)
WORKERS_SEEN_CLAIMS_PRUNER_STARTED: Final[str] = "workers.seen_claims_pruner.started"
WORKERS_SEEN_CLAIMS_PRUNER_STOPPED: Final[str] = "workers.seen_claims_pruner.stopped"
WORKERS_SEEN_CLAIMS_PRUNED: Final[str] = "workers.seen_claims_pruner.pruned"
WORKERS_SEEN_CLAIMS_PRUNE_FAILED: Final[str] = "workers.seen_claims_pruner.prune_failed"

# Backend distributed-path service bundle
WORKERS_BACKEND_BUNDLE_STARTED: Final[str] = "workers.backend_bundle.started"
WORKERS_BACKEND_BUNDLE_START_FAILED: Final[str] = "workers.backend_bundle.start_failed"
WORKERS_BACKEND_BUNDLE_STOP_FAILED: Final[str] = "workers.backend_bundle.stop_failed"

# Heartbeat liveness subscriber (backend-side)
WORKERS_HEARTBEAT_SUBSCRIBER_STARTED: Final[str] = (
    "workers.heartbeat_subscriber.started"
)
WORKERS_HEARTBEAT_SUBSCRIBER_STOPPED: Final[str] = (
    "workers.heartbeat_subscriber.stopped"
)
WORKERS_HEARTBEAT_OBSERVED: Final[str] = "workers.heartbeat_subscriber.observed"
WORKERS_HEARTBEAT_STALE: Final[str] = "workers.heartbeat_subscriber.worker_stale"
WORKERS_HEARTBEAT_SUBSCRIBER_FAILED: Final[str] = "workers.heartbeat_subscriber.failed"
WORKERS_HEARTBEAT_SUBSCRIBER_START_REJECTED: Final[str] = (
    "workers.heartbeat_subscriber.start_rejected"
)

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
WORKERS_TASK_QUEUE_PUBLISH_TIMEOUT: Final[str] = "workers.task_queue.publish_timeout"
WORKERS_TASK_QUEUE_ACK_MALFORMED_FAILED: Final[str] = (
    "workers.task_queue.ack_malformed_failed"
)
WORKERS_TASK_QUEUE_CLAIM_PARSE_FAILED: Final[str] = (
    "workers.task_queue.claim_parse_failed"
)
WORKERS_TASK_QUEUE_STREAM_SETUP_FAILED: Final[str] = (
    "workers.task_queue.stream_setup_failed"
)
WORKERS_TASK_QUEUE_CONSUMER_SETUP_FAILED: Final[str] = (
    "workers.task_queue.consumer_setup_failed"
)
WORKERS_TASK_QUEUE_DEAD_CONSUMER_SETUP_FAILED: Final[str] = (
    "workers.task_queue.dead_consumer_setup_failed"
)

# Lifecycle start-rejected guards (start() called while already running)
WORKERS_WORKER_START_REJECTED: Final[str] = "workers.worker.start_rejected"
WORKERS_SEEN_CLAIMS_PRUNER_START_REJECTED: Final[str] = (
    "workers.seen_claims_pruner.start_rejected"
)
WORKERS_DEAD_LETTER_CONSUMER_START_REJECTED: Final[str] = (
    "workers.dead_letter_consumer.start_rejected"
)

# Main entry point
WORKERS_MAIN_INVALID_WORKER_COUNT: Final[str] = "workers.main.invalid_worker_count"
WORKERS_MAIN_INVALID_EXECUTOR_CONFIG: Final[str] = (
    "workers.main.invalid_executor_config"
)
WORKERS_MAIN_PLACEHOLDER_EXECUTOR_INVOKED: Final[str] = (
    "workers.main.placeholder_executor_invoked"
)
WORKERS_MAIN_SEEN_CLAIMS_WIRED: Final[str] = "workers.main.seen_claims_wired"
WORKERS_MAIN_SEEN_CLAIMS_SKIPPED: Final[str] = "workers.main.seen_claims_skipped"
WORKERS_MAIN_SHUTDOWN_CLEANUP_FAILED: Final[str] = (
    "workers.main.shutdown_cleanup_failed"
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
