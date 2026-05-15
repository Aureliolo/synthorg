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
| `workers.enabled` | bool | `false` | Master switch. |
| `workers.count` | int | `1` | Concurrency per process. |
| `workers.ack_wait_seconds` | int | `30` | JetStream ack timeout before redelivery. |
| `workers.max_deliver` | int | `5` | Max delivery attempts before dead-letter. |
| `workers.stream_name` | str | `SYNTHORG_TASKS` | Stream identifier. |
| `workers.ready_subject_prefix` | str | `tasks.ready` | Subject prefix for ready claims. |
| `workers.dead_subject_prefix` | str | `tasks.dead` | Dead-letter subject prefix. |

NATS-side settings live under `communication.nats_*` (URL, credentials, reconnect timing).

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

Dispatch a claim from the API side:

```python
from synthorg.workers.claim import TaskClaim
from synthorg.workers.dispatcher import JetStreamDispatcher

dispatcher: JetStreamDispatcher = app_state.task_dispatcher
await dispatcher.dispatch(
    TaskClaim(task_id="task-A", new_status="ready"),
)
```

The worker logs:

```
workers.worker.claim_received task_id=task-A
workers.worker.executor.invoked task_id=task-A
workers.worker.executor.completed task_id=task-A outcome=success
```

## Retry and dead-letter

JetStream redelivers a claim on ack-wait timeout (worker crash, slow execution) or `RETRY` outcome. After `max_deliver` attempts the claim moves to the dead-letter subject (`tasks.dead.{task_id}`). Operators monitor the dead-letter subject via `synthorg status --check workers` or directly with `nats consumer report SYNTHORG_TASKS synthorg_workers`.

Idempotency: every claim carries a UUID `idempotency_key`. Workers `mark_seen` the key via `SeenClaimsRepository`; a duplicate redelivery short-circuits to `ack-and-skip` without re-running the executor.

## Observability

Per-claim events:

- `workers.worker.claim_received` (info): claim pulled from JetStream.
- `workers.worker.duplicate_claim_suppressed` (info): redelivery deduped by idempotency key.
- `workers.worker.executor_failed` (warning): executor raised.
- `workers.worker.finalize_failed` (warning): ACK/NAK failed.
- `workers.task_queue.connect_failed` (error): NATS unreachable at start.
- `workers.task_queue.claim_parse_failed` (warning): malformed claim (terminal ACK, never redelivered).

Metrics:

- `synthorg_worker_pool_size` (gauge): configured worker count.
- `synthorg_worker_invocations_total` (counter, `outcome`): per-claim outcomes.
- `synthorg_worker_invocation_duration_seconds` (histogram): per-claim wall time.

Use `synthorg status --check workers` for a quick view; the Grafana `Tasks` dashboard row charts the same metrics over time.

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
| Tasks never run | Dispatcher disabled | Verify `workers.enabled: true`. |
| Duplicate execution | Idempotency disabled | Verify `SeenClaimsRepository` is wired and durable. |
| Claims hit dead-letter | Executor systematically failing | Inspect dead-letter subject for repeating `task_id`; consult task logs. |
| Slow drain on shutdown | `_drain_partial` waiting on NATS | Check `WORKERS_TASK_QUEUE_DRAIN_FAILED`; force-kill after the timeout. |

See [docs/design/distributed-runtime.md](../design/distributed-runtime.md) for the full design.
