---
title: Workers and Background Tasks
description: Configure the JetStream task queue, scale the worker pool, observe dispatch and retry.
---

# Workers and Background Tasks

SynthOrg's distributed task queue runs over NATS JetStream. The dispatcher (`synthorg.workers.dispatcher`) enqueues `TaskClaim` envelopes; workers (`synthorg.workers.worker`) pull from a shared durable consumer and execute the task via an injected executor. This guide walks through configuration, running a worker pool against a local NATS, and observing the dispatch path.

## Concepts

- **Task claim**: a small JSON envelope (`TaskClaim`) carrying `task_id`, `project_id`, `previous_status`, `new_status`, and an `idempotency_key` for redelivery dedup.
- **JetStream stream**: `SYNTHORG_TASKS` with `WorkQueuePolicy` and exclusive subjects (`tasks.ready.>` and `tasks.dead.>`).
- **Durable consumer**: shared `synthorg_workers` consumer; every worker pulls from the same name so JetStream handles load distribution.
- **Executor**: a pluggable async callable that consumes a claim and returns `TaskClaimStatus.SUCCESS` / `FAILED` / `RETRY`.

## Configuration

`QueueConfig` carries the queue settings:

| Key | Type | Default | Purpose |
|---|---|---|---|
| `queue.enabled` | bool | `false` | Master switch. Ships `false`: single-node runs in-process, multi-instance opts in. |
| `queue.workers` | int | `4` | Default worker count for `synthorg worker start`. |
| `queue.ack_wait_seconds` | int | `300` | JetStream ack deadline before redelivery. |
| `queue.max_deliver` | int | `3` | Deliveries before the worker republishes the claim to the dead subject. |
| `queue.heartbeat_interval_seconds` | int | `30` | Working-ack + liveness cadence. Validated strictly `< ack_wait_seconds`. |
| `queue.max_ack_pending` | int | `16` | Backpressure: max unacked claims in flight across the shared consumer. |
| `queue.stream_max_msgs` | int | `100000` | Work-queue stream message cap. |
| `queue.stream_max_bytes` | int | `1073741824` | Work-queue stream byte cap (1 GiB). |
| `queue.prune_interval_seconds` | int | `3600` | Cadence the backend prunes expired dedup rows. |
| `queue.stream_name` | str | `SYNTHORG_TASKS` | Stream identifier. |
| `queue.ready_subject_prefix` | str | `synthorg.tasks.ready` | Subject prefix for ready claims. |
| `queue.dead_subject_prefix` | str | `synthorg.tasks.dead` | Dead-letter subject prefix. |

NATS-side settings live under `communication.message_bus.nats` (URL, credentials, reconnect timing). `queue.enabled: true` requires a reachable NATS JetStream server there.

## Worker authentication

The worker pool's HTTP executor authenticates back to the backend with a per-deployment bearer token. The token source is the `SYNTHORG_WORKER_AUTH_TOKEN` environment variable, read once at worker construction time (see `src/synthorg/workers/__main__.py`).

Operational guidance:

- Treat the token like any other long-lived credential: store it in your secrets manager (Vault, AWS Secrets Manager, etc.) and inject it into the worker container via `env` rather than baking it into the image.
- Rotate the token on a schedule that matches the rest of your service-account hygiene (typically every 90 days). Rotation is a rolling restart of the worker pool with the new value; nothing else changes.
- Always set `SYNTHORG_API_BASE_URL` to an HTTPS endpoint in production. Sending the bearer token over plain HTTP discloses it on the wire.
- The token is NEVER logged. Worker observability events redact the `Authorization` header; if you see a token in a worker log it is a regression. File an issue.
- Workers refuse to start with an empty token (`SYNTHORG_WORKER_AUTH_TOKEN=""`); failing fast at boot is intentional so an unauthenticated executor never reaches a real backend.

## Worked example: run the worker pool

Start a local NATS:

```bash
docker run -d --name synthorg-nats -p 4222:4222 nats:2.10-alpine -js
```

Run the worker module:

```bash
SYNTHORG_NATS_URL=nats://localhost:4222 \
  uv run python -m synthorg.workers --workers 2
```

The worker:

1. Calls `JetStreamTaskQueue.start()` which creates the stream and durable consumer.
2. Polls the consumer for claims with a per-fetch timeout.
3. Runs the configured executor on each claim.
4. ACKs on `SUCCESS`/`FAILED`; NAKs with backoff on `RETRY`.

Claims are not dispatched by hand. The in-process `DistributedDispatcher` is registered as a `TaskEngine` observer (only when `queue.enabled` is true) and publishes a `TaskClaim` to `synthorg.tasks.ready.<task_id>` automatically when a task transitions into `ASSIGNED`. For a manual smoke test against a running NATS you can publish directly:

```python
from synthorg.workers.claim import TaskClaim

await task_queue.publish_claim(
    TaskClaim(task_id="task-A", new_status="assigned"),
)
```

The worker logs:

```text
workers.worker.claim_received task_id=task-A
workers.executor.http_invoked task_id=task-A
workers.executor.http_terminal task_id=task-A
```

## Retry, ack-extension and dead-letter

JetStream redelivers a claim on ack-wait timeout (worker crash) or a `RETRY` outcome. While the executor runs, the worker sends a periodic working-ack (`in_progress`) every `heartbeat_interval_seconds` so an execution longer than `ack_wait_seconds` does not lapse the deadline and trigger a concurrent duplicate run; this is why `heartbeat_interval_seconds` is validated strictly below `ack_wait_seconds`.

`WorkQueuePolicy` does **not** auto-route to a dead subject on `max_deliver` exhaustion; it terminates the message. So on the final delivery the worker republishes the claim to `synthorg.tasks.dead.<task_id>` and terminal-acks. The backend `DeadLetterConsumer` (its own durable consumer on the dead subject) then transitions the task to `FAILED`, deduplicating on the idempotency key. A dead claim that cannot be proven failed raises `WorkerDeadLetterError` rather than acking into silent loss. Inspect the dead path with `nats consumer report SYNTHORG_TASKS`.

Idempotency: every claim carries a UUID `idempotency_key`. Workers `mark_seen` the key via `SeenClaimsRepository` after a terminal outcome; a duplicate redelivery short-circuits to ack-and-skip without re-running the executor. The backend prunes expired dedup rows every `prune_interval_seconds` (`SeenClaimsPruner`) so the table stays bounded.

## Backpressure

`max_ack_pending` caps total unacked claims in flight across the shared durable consumer: JetStream stops delivering once that many are outstanding, so a slow worker pool throttles intake instead of buffering unboundedly. `stream_max_msgs` / `stream_max_bytes` bound the work-queue stream on disk. These are set at stream/consumer creation, so a change requires a restart of the backend.

## Observability

There is no Prometheus worker series. Worker liveness and the distributed-path lifecycle are surfaced through the structured-log pipeline; consume these events (constants in `synthorg.observability.events.workers`):

- `workers.worker.claim_received` (info): claim pulled from JetStream.
- `workers.worker.duplicate_claim_suppressed` (info): redelivery deduped by idempotency key.
- `workers.worker.executor_failed` (warning): executor raised.
- `workers.worker.finalize_failed` (warning): ack/nack failed.
- `workers.worker.ack_extend_failed` (warning): a working-ack send failed (non-fatal).
- `workers.worker.claim_dead_lettered` (warning): claim exhausted `max_deliver`, republished to the dead subject.
- `workers.dead_letter.transitioned` (warning): a dead-lettered task was driven to `FAILED`.
- `workers.dead_letter.duplicate_suppressed` (info): redelivered dead message deduped.
- `workers.dead_letter.failed` (error): a dead claim could not be failed (paired with a raised `WorkerDeadLetterError`).
- `workers.heartbeat_subscriber.observed` (info) / `workers.heartbeat_subscriber.worker_stale` (warning): worker liveness.
- `workers.seen_claims_pruner.pruned` (info): expired dedup rows reclaimed.
- `workers.task_queue.connect_failed` (error): NATS unreachable at start.
- `workers.task_queue.claim_parse_failed` (warning): malformed claim (terminal ack, never redelivered).

Tail worker liveness directly with `nats sub 'synthorg.workers.heartbeat.>'` (core NATS, not JetStream).

## Pluggable executor

The default executor in `src/synthorg/workers/__main__.py` is a thin wrapper that fetches the task and dispatches to the agent runtime. Operators can supply a custom executor by replacing `run_worker_pool`'s `executor` argument; the contract is:

```python
async def my_executor(claim: TaskClaim) -> TaskClaimStatus:
    # ... custom dispatch logic ...
    return TaskClaimStatus.SUCCESS
```

The executor is responsible for the HTTP-callback that transitions the task status; see the existing `synthorg.communication.async_tasks.callbacks` module for the expected shape.

## Diagnostic checklist

| Symptom | Likely cause | Mitigation |
|---|---|---|
| Workers never start | NATS unreachable | Check `synthorg.workers.task_queue.connect_failed`; verify `SYNTHORG_NATS_URL`. |
| Tasks never run | Dispatcher disabled | Verify `queue.enabled: true` and a reachable NATS server. |
| Duplicate run on long task | ack-extension misconfigured | Ensure `heartbeat_interval_seconds < ack_wait_seconds`; check `workers.worker.ack_extend_failed`. |
| Duplicate execution | Idempotency disabled | Verify `SeenClaimsRepository` is wired and durable. |
| Claims hit dead-letter | Executor systematically failing | Inspect dead-letter subject for repeating `task_id`; consult task logs. |
| Slow drain on shutdown | `_drain_partial` waiting on NATS | Check `WORKERS_TASK_QUEUE_DRAIN_FAILED`; force-kill after the timeout. |

See [docs/design/distributed-runtime.md](../design/distributed-runtime.md) for the full design.
