---
title: Distributed Runtime
description: Pluggable distributed bus backend design, NATS JetStream first implementation, and distributed task queue hook into TaskEngine. Opt-in, in-memory bus remains the default.
---

# Distributed Runtime

!!! warning "Designed behaviour; runtime in active development"

    This page is the source of truth for the **designed** behaviour of this subsystem. The dispatch plumbing (NATS JetStream queue and worker pool) exists, but the task-execute endpoint currently advances task state without invoking an agent; end-to-end distributed execution depends on the agent runtime, which is in active development (see the [Roadmap](../roadmap/index.md)).

SynthOrg runs in a single Python process by default. Agents communicate over an in-memory `MessageBus` (per-(channel, subscriber) `asyncio.Queue`) and the `TaskEngine` dispatches work through its own single-writer mutation queue inside that same process. For a laptop running one synthetic org, this is the right answer: lowest latency, no extra containers, nothing to operate.

This page describes the **first distributed backend** that plugs into the existing `MessageBus` protocol without changing it, and the **distributed task queue** that sits on top of that backend. Both are opt-in. The in-memory path stays the default and must remain byte-identical in behavior for users who do not turn on distribution.

This page is for:

- Operators deploying SynthOrg beyond a single host
- Contributors adding a new distributed backend behind the pluggable `MessageBus` factory after the first one ships
- Reviewers of Issues #236 and #237

If you are running SynthOrg on one machine for one organization, you can skip this page. Nothing changes for you.

---

## Problem Statement

The in-memory bus hits three hard limits as soon as a deployment wants to move past single-process operation:

1. **No durability.** Messages in transit and channel history (`get_channel_history`) live inside Python process memory. A crash or restart vaporizes unread messages, queued task handoffs, and the last N messages per channel used for debugging and replay.
2. **No multi-process execution.** `asyncio.Queue` cannot cross process boundaries. All agent execution, all tool invocations, and all LLM calls funnel through one Python event loop. Horizontally scaling the execution layer, or isolating expensive agents on a dedicated host, is impossible without a transport that works across processes.
3. **No external observability.** The bus is invisible to anything outside Python. You cannot inspect channels, replay history, or measure queue depth from a terminal, a Prometheus scrape, or a Grafana dashboard without adding application-level code for every metric.

These limits are acceptable for `Phase 1: Local Single-Process` in the [Scaling Path](../roadmap/future-vision.md#scaling-path). They are the reason `Phase 2: Local Multi-Process` exists.

---

## Non-Goals

- **Not a protocol refactor.** `src/synthorg/communication/bus_protocol.py` is stable and does not change. Both backends implement the exact same Protocol surface.
- **Not a general upgrade.** The distributed backend is slower (network hop), harder to operate (extra container), and more configuration (URL, credentials, retention). Users who do not need multi-process execution should not turn it on.
- **Not replacing the in-memory default.** `internal` remains the default in `MessageBusConfig.backend` and in the Go CLI `synthorg init` picker. Distribution is opt-in, once.
- **Not changing single-writer semantics in `TaskEngine`.** The distributed task queue hooks into `register_observer`; it does not bypass the mutation queue. Workers call back to the backend HTTP API to transition tasks, which routes through the same single-writer path as today.

---

## Transport Evaluation

### Candidates considered

Five candidates were evaluated against the constraints of the existing `MessageBus` protocol (pull-model `receive()`, per-(channel, subscriber) queues, bounded per-channel history) and the deployment shape (single-host Docker Compose today, multi-host later).

- **NATS JetStream** (Apache 2.0, ~20 MB Go binary)
- **Valkey Streams** / Redis Streams (BSD for Valkey 7.2+, SSPL/RSALv2 for Redis 7.4+)
- **RabbitMQ** (MPL 2.0, Erlang OTP)
- **Kafka KRaft mode** (Apache 2.0, JVM)
- **ZeroMQ brokerless** (MPL 2.0, Python library via pyzmq)

### Evaluation matrix

| Dimension | NATS JetStream | Valkey/Redis Streams | RabbitMQ | Kafka (KRaft) | ZeroMQ brokerless |
|---|---|---|---|---|---|
| Fit for pull-model `receive()` | Excellent (pull consumers + FetchBatch) | Good (XREADGROUP BLOCK) | OK (basic_consume + prefetch) | OK (consumer.poll with timeout) | Poor (callback-style, DIY pull) |
| Per-subscriber queue primitive | Durable pull consumer per subscriber | Consumer group + consumer name | Auto-delete queue bound to exchange | Consumer group + partition assignment | DIY, no built-in primitive |
| History / replay | `DeliverByStartSequence` / `DeliverAll` | `XRANGE` / `XREAD` from id | Weak (Streams plugin is separate) | Native, offset seek | None, DIY sqlite sidecar |
| Delivery guarantees | At-least-once, per-subject ordering | At-least-once (PEL + XACK) | At-least-once with acks | At-least-once (committed offsets) | At-most-once by default |
| Dead-letter | `max_deliver` + DLQ subject | Manual via XCLAIM + idle | Native DLX | Manual DLQ topic pattern | DIY |
| Ordering | Per-subject FIFO | Per-stream FIFO | Per-queue FIFO | Per-partition FIFO (partition key) | None guaranteed |
| Work-queue fit (task queue) | `WorkQueuePolicy` native | Consumer group doubles as work queue | Classic work-queue pattern | Awkward (partitions != dynamic workers) | DIY |
| Docker footprint | ~20 MB image, ~15 MB RAM idle | ~40 MB image, ~30 MB RAM | ~200 MB image, ~150 MB RAM | ~400 MB image, ~512 MB RAM (JVM) | Zero extra container |
| Python client maturity | `nats-py` (official, asyncio, active) | `redis.asyncio` (bundled in redis-py) | `aio-pika` (mature, asyncio) | `aiokafka` (mature, heavy) | `pyzmq` (mature, callback-first) |
| License | Apache 2.0 | BSD (Valkey) / non-OSS (Redis 7.4+) | MPL 2.0 | Apache 2.0 | MPL 2.0 |
| Spec status (`architecture/tech-stack.md`) | Listed as the shipped distributed backend | Evaluated, not adopted | Evaluated, not adopted | Evaluated, not adopted | Evaluated, not adopted |

### Per-candidate narratives

**NATS JetStream.** Pull consumers map one-to-one onto the `receive(timeout=t)` semantics the existing Protocol exposes. Per-subscriber durable consumers replace per-(channel, subscriber) `asyncio.Queue` without any impedance mismatch. A single stream with `LimitsPolicy` and `MaxMsgsPerSubject` preserves the existing bounded-history semantic natively, without application-level bookkeeping. The task queue in Phase 4 uses a second stream with `WorkQueuePolicy` for the claim/ack lifecycle. Footprint is the smallest of the credible candidates, which matters for the default case where a user opts in and expects "run `docker compose --profile distributed up`" to be cheap. License is Apache 2.0, client is official and asyncio-native.

**Valkey/Redis Streams.** Functionally a close second. `XADD` + `XREADGROUP BLOCK` map cleanly to `publish()` / `receive()`, and consumer groups give per-subscriber claims. The blocker is licensing: Redis 7.4+ is now SSPL/RSALv2 (non-OSS), which matters for a BUSL-licensed project that wants to stay compatible with downstream packaging. The mitigation is pinning Valkey 7.2+ (BSD fork, drop-in via `redis.asyncio`). If the first distributed backend were Redis/Valkey, the design doc and install instructions would have to lead with this license distinction, which is operational friction for a feature most users never touch. Workable but adds narrative weight.

**RabbitMQ.** Very mature, battle-tested, and `aio-pika` is a known-good async client. The problems are weight and replay. A RabbitMQ broker is ~200 MB, boots an Erlang VM, and brings a management plugin that expects to be configured. Replay / bounded history is weak unless the Streams plugin is enabled separately, which would require us to manage two RabbitMQ primitives (classic queues for delivery, streams for history). For a first distributed backend whose goal is opt-in-and-forget, the operational surface is too big.

**Kafka (KRaft).** Strongest replay story in the list and genuinely best-in-class for per-partition ordering at scale. Overkill for a first distributed backend in a pre-alpha project: ~400 MB JVM image, ~512 MB RAM idle, partition planning, consumer group rebalancing, and a work-queue story that awkwardly reuses partitions as worker slots. Good fit later if SynthOrg's analytics side ever needs Kafka; not a good first step.

**ZeroMQ brokerless.** The ClawTeam research note referenced in Issue #236 uses this pattern. Zero extra containers, pure Python library. But ZMQ gives us sockets, not a bus: no durability, no replay, no dead-letter, no built-in per-subscriber queues, and delivery guarantees are at-most-once unless we layer on a DIY sqlite sidecar. Attractive for zero-container deployment, rejected because "first distributed backend" should be a real distributed system, not a partially-rebuilt one.

### Decision: NATS JetStream

NATS JetStream wins on three dimensions that matter most for a first distributed backend:

1. **Protocol fit without impedance mismatch.** The pull-model `receive()` Protocol was written before NATS was considered, yet JetStream pull consumers are a one-line mapping to the same semantics. Every other candidate requires adapter code to bridge the push/pull gap, and at least one (Kafka) requires partition planning that has no analogue in the current Protocol.

2. **Operational smallness.** The single ~20 MB Go binary is a rounding error against the Wolfi Python base image. Users who flip the switch pay for one more service to operate, not four. This matters for an opt-in feature most users do not need, because the expected value of "trying distribution once" has to be high enough to survive the friction of adding a container.

3. **Future-proof without lock-in.** JetStream's primitives (streams, durable consumers, KV buckets, work queues) map naturally onto what the design needs today *and* leave room for what the project will want later (leaf nodes for multi-region, KV for distributed config). Apache 2.0 license, official asyncio client, active project.

NATS JetStream is the shipped distributed backend. The comparison above preserves the full evaluation so that a future change of backend (or addition of a second adapter) can be audited against the same criteria.

#### NATS client library

The project uses `nats-py==2.14.0`. A scoped `filterwarnings` entry in `pyproject.toml` suppresses the `asyncio.iscoroutinefunction` deprecation warning from `nats-py` on Python 3.14 until an upstream fix lands. See [docs/architecture/decisions.md](../architecture/decisions.md) for the full decision record including the evaluation of alternative clients.

---

## Bus Backend Design

The `MessageBus` Protocol in `src/synthorg/communication/bus_protocol.py` is stable. Both backends implement every method of the existing Protocol verbatim. The rest of this section describes how the NATS backend maps Protocol semantics onto JetStream primitives.

### Stream layout

A single JetStream stream named `SYNTHORG_BUS` holds all message bus traffic.

- **Retention policy**: `LimitsPolicy` with `MaxMsgsPerSubject = config.retention.max_messages_per_channel`. This preserves the existing per-channel history-bound semantic natively, without application-level bookkeeping. The in-memory backend bounds each channel's `deque`; the NATS backend bounds each subject's retained messages. Same semantic, different mechanism.
- **Subject taxonomy**:
    - `synthorg.bus.channel.<sanitized_name>` for `TOPIC` and `BROADCAST` channels
    - `synthorg.bus.direct.<a>:<b>` for lazily-created `DIRECT` channels (where `a < b` are the sorted agent IDs, matching the in-memory `@a:b` convention)
- **Sanitization**: JetStream subject tokens accept alphanumerics, `-`, `_`, and `.` as a separator. Channel names with any other character get a stable sanitization pass before they become subject tokens. The original channel name stays in the `Channel` registry so protocol callers see the names they passed in.

The Phase 4 task queue uses a **separate** stream `SYNTHORG_TASKS` with `WorkQueuePolicy` retention. Separation matters because the two streams have incompatible retention requirements: the bus retains the last N messages per subject, the task queue deletes messages after ack.

### Per-message TTL (NATS 2.11+)

The stream is configured with `allow_msg_ttl=True` so individual messages can expire independently of the stream-level retention policy. The `MessageBus.publish()` and `send_direct()` methods accept an optional `ttl_seconds` parameter; when set, the message expires after that duration on the server regardless of `MaxMsgsPerSubject`. When `None` (default), the stream's LimitsPolicy governs retention as before.

- **Wire protocol**: uses nats-py's native `msg_ttl` parameter on `JetStreamContext.publish()` (nats-py 2.14.0+).
- **In-memory backend**: accepts `ttl_seconds` for protocol conformance but ignores it (retention is deque-based).

### Batch publishing

`MessageBus.publish_batch()` publishes multiple messages using JetStream's pipelined async publish API for reduced round-trip overhead. The implementation is three-phased:

1. **Validation (fail-fast)**: resolve all target channels and serialize all payloads. If any channel is missing or any payload exceeds `MAX_BUS_PAYLOAD_BYTES`, the batch fails before any message is sent.
2. **Pipelined publish**: fire all `publish_async()` calls, buffering acknowledgments on the server side.
3. **Ack collection**: `publish_async_completed()` waits for all server acks. Any individual publish failures are collected and surfaced as an `ExceptionGroup`.

The stream is also configured with `allow_atomic=True` for future nats-py atomic batch support. The in-memory backend publishes each message sequentially for protocol conformance.

### Subscriber to durable consumer mapping

Each `(channel_name, subscriber_id)` pair in the in-memory backend owns its own `asyncio.Queue`. The NATS backend replaces that with one JetStream durable pull consumer per pair.

- **Durable name**: `<sanitized_channel>__<sanitized_subscriber>`. Double underscore separator because JetStream durable names cannot contain `.` or spaces.
- **Filter subject**: the subject for the channel (`synthorg.bus.channel.<name>` or `synthorg.bus.direct.<a>:<b>`).
- **Ack policy**: explicit ack. The backend acks on successful fetch (see below).
- **Max deliver**: 1 at the bus layer (we do not retry; callers do not expect retry semantics from `receive()`). The Phase 4 task queue uses its own consumers with higher `max_deliver`.
- **`receive(timeout=t)` implementation**: `consumer.fetch(batch=1, timeout=t)`. Returns a `DeliveryEnvelope` on success, `None` on timeout or shutdown.

### Ack semantics

The `MessageBus` Protocol does not expose ack to callers. `receive()` returns a `DeliveryEnvelope` and the message is gone from the caller's point of view. The in-memory backend achieves this by dequeueing from `asyncio.Queue` and never re-enqueueing.

The NATS backend matches that semantic by acking immediately on successful fetch, before returning the envelope to the caller. Consequences:

- **At-most-once from the caller's point of view.** If a caller crashes between `receive()` returning and the caller processing the envelope, the message is gone. Same as in-memory.
- **At-least-once is not promised at the bus layer.** The Phase 4 task queue does not rely on bus-layer at-least-once. It speaks to JetStream directly with manual ack and its own `max_deliver` configuration, precisely because worker crash recovery needs different semantics than bus delivery.

This is the right split. The bus layer gives callers a simple pull-and-forget experience matching the existing Protocol. The task queue layer gets the delivery guarantees it needs by talking to JetStream under the bus.

### Channel registry

The in-memory backend keeps a `dict[str, Channel]` registry that holds pre-configured channels (created at `start()` from `CommunicationConfig.channels`) plus lazily-created DIRECT channels. The NATS backend uses a hybrid:

- **Pre-configured channels**: loaded from `CommunicationConfig.channels` at `start()` and held in a local `dict[str, Channel]`, identical to the in-memory path. Config is the source of truth. Multiple backends started with the same config have the same channel set.
- **Lazy DIRECT channels**: registered in a JetStream **KV bucket** named `SYNTHORG_BUS_CHANNELS`. When `send_direct(sender, recipient)` creates a new DIRECT channel, the backend writes `{channel_name: channel_json}` to the KV bucket. Other backends subscribed to the same NATS cluster see the new channel on their next `list_channels()` or `get_channel()` call.

The KV bucket is scoped to dynamic channels only. Pre-configured channels never hit the bucket because config is shared across processes by definition. This is deliberately simple: the KV bucket is the escape hatch for the one case where in-process state is genuinely insufficient (cross-process discovery of lazy channels), not a general-purpose distributed config store.

### Shutdown

The in-memory backend uses a `None` sentinel to wake blocked `receive()` calls on shutdown. The NATS backend does the equivalent via task cancellation:

1. `stop()` sets `_running = False` and cancels all outstanding `consumer.fetch()` tasks.
2. Inside each `receive()` call, cancellation surfaces as `asyncio.CancelledError` from the `fetch()` await.
3. The `receive()` implementation catches `CancelledError` and returns `None`, matching the in-memory sentinel semantic.

Callers that are blocked in `receive()` at shutdown time get back `None`, exactly as they do with the in-memory backend.

### History and replay

`get_channel_history(channel_name, limit=N)` queries JetStream for the last `N` messages on the channel's subject. The implementation lives in a new `bus/persistence.py` helper (`HistoryAccessor` protocol + two implementations) so the query path is unit-testable independently of the driver:

- **`DequeHistoryAccessor`**: wraps the existing `deque` already used by the in-memory backend. Zero behavior change.
- **`JetStreamHistoryAccessor`**: gets the stream's current `last_seq` via `stream.info()`, then fetches the last `N` messages by sequence with a subject filter. Returns them in chronological order.

Both accessors satisfy the same `HistoryAccessor` protocol so each backend's `get_channel_history` is a one-liner delegating to its accessor.

### Connection lifecycle

`nats-py` handles auto-reconnect transparently. The backend emits three new observability events on top of the existing bus event inventory:

- `COMM_BUS_CONNECTED`: initial connection established or reconnection succeeded
- `COMM_BUS_RECONNECTING`: client is attempting to reconnect after a disconnect
- `COMM_BUS_DISCONNECTED`: connection lost (paired with a later `RECONNECTING` or `CONNECTED`)

All existing bus events (`COMM_BUS_STARTED`, `COMM_CHANNEL_CREATED`, `COMM_MESSAGE_PUBLISHED`, `COMM_MESSAGE_DELIVERED`, `COMM_SUBSCRIPTION_CREATED`, etc.) are emitted by the NATS backend with the same payload shape as the in-memory backend, so observability dashboards and log filters work identically regardless of which backend is active.

---

## Task Queue Design

The task queue in Phase 4 builds on top of the NATS backend but does **not** go through the `MessageBus` Protocol. It is a separate concern with different delivery semantics (at-least-once, manual ack, retry with backoff) that the bus layer does not promise.

### Stream and subjects

A second JetStream stream named `SYNTHORG_TASKS` with `WorkQueuePolicy` retention. `WorkQueuePolicy` means: messages are deleted from the stream once any consumer successfully acks them. This is the native JetStream primitive for work queues and matches classic task-queue semantics.

- **Subject (ready)**: `synthorg.tasks.ready.<task_id>` for tasks the TaskEngine has transitioned to a runnable state
- **Subject (dead)**: `synthorg.tasks.dead.<task_id>`, the dead-letter subject for tasks that exceeded `max_deliver`

### Worker lifecycle

Workers are separate Python processes launched via `synthorg worker start` (Phase 4 adds the Go CLI wrapper). Each worker:

1. **Connects** to NATS and to the backend HTTP API (separate connections; NATS for claim, HTTP for transitions).
2. **Claims** a ready task by fetching from `SYNTHORG_TASKS` with a durable consumer. Manual ack. Two independent controls bound the consumer. `ack_wait` (a time deadline, default 300 seconds) is the per-message window within which a claim must be acked or extended before JetStream redelivers it; it is configured below any `heartbeat_interval_seconds` so an in-progress working-ack extends the deadline before it lapses. `max_ack_pending` (a count) is the cap on unacknowledged claims in flight across the shared consumer: the backpressure lever that makes a slow pool throttle intake instead of buffering unboundedly. The two are orthogonal, one bounding per-message latency and the other bounding concurrent inflight executions.
3. **Executes** the task via the same agent runtime code used by the in-process path today, reusing the existing agent execution machinery. No duplicated logic. While the executor runs, the worker sends a periodic working-ack (JetStream `in_progress`) every `heartbeat_interval_seconds` so an execution longer than `ack_wait` does not lapse the deadline and trigger a concurrent redelivery. `heartbeat_interval_seconds` is validated to be strictly below `ack_wait_seconds` so the first extension always lands before the deadline.
4. **Transitions** the task on success or failure by calling the backend HTTP API (`POST /api/v1/tasks/{id}/execute`), which routes through the normal `TaskEngine` mutation queue. Single-writer preserved.
5. **Acks** the NATS message on successful transition. Nacks on execution failure to trigger redelivery.
6. **Heartbeats** on a background task by publishing a `WorkerHeartbeat` to `synthorg.workers.heartbeat.<worker_id>` at `heartbeat_interval_seconds`. This is a **core NATS** publish (at-most-once), not JetStream: it must never enter the `WorkQueuePolicy` task stream. The backend's heartbeat subscriber observes these and surfaces liveness through the structured-log pipeline (`workers.heartbeat_subscriber.observed` per beat, `workers.heartbeat_subscriber.worker_stale` when a previously-seen worker stops beating). Crash-recovery correctness does NOT depend on the heartbeat: a dead worker's in-flight claim is redelivered by JetStream `ack_wait`. The heartbeat is operator visibility only.

### Single-writer preservation

The `TaskEngine` single-writer invariant says: only the engine's internal mutation queue changes task state. Any worker, observer, or external caller that wants to change task state must go through the engine.

The distributed worker preserves this by calling the backend HTTP API for every task transition. The API handler calls `engine.transition_task(...)`, which enqueues a `TransitionTaskMutation` into the mutation queue, which the background processor applies sequentially. Workers never touch the persistence layer directly, never call the mutation-apply functions directly, and never hold task state locally beyond what the running agent needs.

### Failure handling

- **Execution failure (retryable)**: worker nacks the NATS message. JetStream redelivers after `ack_wait`. `WorkQueuePolicy` does **not** route to a dead subject when `max_deliver` is exhausted; it terminates the message. So on the **final** delivery (`num_delivered == max_deliver`) the worker itself republishes the claim to `synthorg.tasks.dead.<task_id>` and then terminal-acks. The publish-then-ack ordering is load-bearing: a failed republish leaves the claim un-acked and raises loudly rather than silently dropping the task. The backend's `DeadLetterConsumer` (its own durable consumer on the dead subject) transitions the task to `FAILED`, deduplicating on the claim's idempotency key so a redelivered dead message never double-transitions. This in-process consumer goes through `TaskEngine.transition_task`, the same single-writer mutation path the dispatcher observes.
- **Worker crash**: the NATS consumer's ack deadline expires, JetStream redelivers to another worker automatically. No application-level liveness code needed for correctness.
- **Execution failure (terminal)**: worker resolves the task through `POST /api/v1/tasks/{id}/execute` and acks. The task ends in a terminal status without another redelivery.

### Dispatcher hook

The existing `TaskEngine.register_observer(callback)` at `src/synthorg/engine/task_engine.py:135` is the clean extension seam. Phase 4 adds a distributed dispatcher that:

1. Registers as an observer at engine startup, but only when `config.queue.enabled` is true.
2. Watches `TaskStateChanged` events for the transition to the runnable state.
3. On match, publishes a claim message to `synthorg.tasks.ready.<task_id>` on the `SYNTHORG_TASKS` stream.

Because the dispatcher only observes and publishes, the engine's single-writer path is unchanged. Reads still bypass the mutation queue as they do today. Tests with `queue.enabled = false` show byte-identical behavior to the pre-PR engine.

---

## Migration Path

### Defaults

`internal` is and stays the default in `MessageBusConfig.backend` and in the Go CLI `synthorg init` picker. Existing deployments that do nothing see zero behavior change: same bus implementation, same single-process execution, same config file.

The distributed **task queue** is governed by a separate switch, `config.queue.enabled`, and **ships `false`**. This is the deliberate, validated shipped default:

- **Single-node (the default, `queue.enabled: false`)**: tasks dispatch in-process through the `TaskEngine` mutation queue. No NATS container, no worker processes, lowest latency. This is correct for one synthetic organisation on one host and is what the overwhelming majority of deployments should run. Nothing in this section applies to them.
- **Multi-instance (opt-in, `queue.enabled: true`)**: required only when execution must span processes or hosts. The operator sets `queue.enabled: true`, points `communication.message_bus.nats` at a reachable NATS JetStream server, and runs one or more worker pools via `synthorg worker start`. With the switch off the dispatcher observer, the JetStream queue, the dead-letter consumer, the dedup pruner, and the heartbeat subscriber are never constructed, so the in-process path is byte-identical to a build without the distributed code.

The default is `false` because the distributed path costs an extra service to operate and a network hop per dispatch for a capability most deployments never need; turning it on is a conscious scaling decision, not a default users back into.

### Config

Users opt in by editing `config.yaml`:

```yaml
communication:
  message_bus:
    backend: nats          # opt in, default was "internal"
    nats:
      url: nats://localhost:3003    # host port for docker-compose profile; use nats://nats:4222 when the backend runs inside docker
      credentials_path: null              # optional, for secured clusters
      stream_name_prefix: SYNTHORG
      reconnect_time_wait_seconds: 2.0    # gap between reconnect attempts
    channels:
      - "#all-hands"
      - "#engineering"
      # (unchanged from default set)
    retention:
      max_messages_per_channel: 1000   # unchanged, passed through to MaxMsgsPerSubject

queue:
  enabled: true              # opt in; ships false (single-node default)
  ack_wait_seconds: 300      # JetStream ack deadline before redelivery
  max_deliver: 3             # deliveries before the worker dead-letters the claim
  heartbeat_interval_seconds: 30   # working-ack + liveness cadence; MUST be < ack_wait_seconds
  max_ack_pending: 16        # backpressure: max unacked claims in flight
  stream_max_msgs: 100000    # work-queue stream message cap
  stream_max_bytes: 1073741824   # work-queue stream byte cap (1 GiB)
  prune_interval_seconds: 3600   # cadence the backend prunes expired dedup rows
```

The `nats` sub-block is required when `backend` is `nats` (validated at config load time). When `backend` is `internal`, the `nats` sub-block is ignored if present. The `queue` block is independent of the bus `backend`: `queue.enabled: true` additionally requires a reachable `communication.message_bus.nats` server. `heartbeat_interval_seconds` is validated strictly below `ack_wait_seconds` at config load so the working-ack always fires before the ack deadline.

### CLI picker at `synthorg init`

First-run users hit the picker in the Go CLI, which is unbiased and surfaces the trade-off explicitly.

The picker is a generic `PickOne[T]` helper in `cli/internal/ui/picker.go` wrapping the `charmbracelet/huh` library already in the CLI dependency graph. The `BusBackends` registry in `cli/internal/ui/options.go` is data-driven: each entry has an ID, label, one-line summary, bullet-list pros, bullet-list cons, a default flag, and the value it writes to the config. Adding a new backend later is one struct literal in `options.go` plus the matching Python implementation in `src/synthorg/communication/bus/`. No UI code changes.

Non-interactive mode honors the existing `--yes` / `SYNTHORG_YES` convention by writing `internal` without prompting. A new `--bus-backend` flag on `init` lets scripted setup pick any value in the registry. Invalid values exit with code 2 and a message listing the valid backends.

### Unbiased backend copy

These are the exact strings the picker shows and the exact framing this design page uses. Neither backend is marked "recommended". `internal` is marked **default** because it is the config default, not because it is better.

**In-process queue (`internal`): default**

Runs inside the backend container using `asyncio` queues. No extra services.

Pros:

- Zero setup, no extra container
- Microsecond-latency delivery
- Nothing extra to operate or monitor
- Works offline

Cons:

- Single Python process only
- Messages and task queue are lost on backend crash
- No replay after restart
- Not observable from outside Python

**NATS JetStream (`nats`)**

Runs as a separate ~20 MB container with file-backed streams.

Pros:

- Multi-process and multi-host agent execution
- Messages and task queue survive backend crashes
- Replay from any stream offset
- At-least-once delivery for the task queue with automatic redelivery
- Inspectable via the `nats` CLI and Prometheus metrics
- Prerequisite for `synthorg worker start`

Cons:

- Adds one container (~20 MB image, ~15 MB RAM idle)
- Network hop adds milliseconds of latency
- One more service to monitor and upgrade
- Additional configuration surface (URL, credentials, stream retention)

---

## Observability

### Event parity

Every bus event emitted by the in-memory backend is emitted identically by the NATS backend, using the same constants from `synthorg.observability.events.communication`. A dashboard built against the in-memory bus works unchanged against the NATS bus.

### New connection events

The NATS backend adds three events scoped to connection lifecycle:

- `COMM_BUS_CONNECTED`: initial connection or successful reconnect
- `COMM_BUS_RECONNECTING`: reconnect attempt in progress
- `COMM_BUS_DISCONNECTED`: connection lost

These are scoped to the NATS backend. The in-memory backend never emits them (there is no connection).

### Task-queue events

The distributed task path emits structured events under
`synthorg.observability.events.workers` (variable always `logger`).
Operator-relevant ones:

- `workers.worker.claim_dead_lettered` (WARNING): a claim exhausted `max_deliver` and was republished to the dead subject by the worker.
- `workers.dead_letter.transitioned` (WARNING): the dead-letter consumer drove a dead-lettered task to `FAILED`.
- `workers.dead_letter.duplicate_suppressed` (INFO): a redelivered dead message was deduped (no double-transition).
- `workers.dead_letter.failed` (ERROR): a dead claim could not be proven failed; paired with a raised `WorkerDeadLetterError` so the loss is loud, never silent.
- `workers.worker.ack_extend_failed` (WARNING): a working-ack send failed (non-fatal; the next one or JetStream redelivery recovers).
- `workers.heartbeat_subscriber.observed` (INFO) / `workers.heartbeat_subscriber.worker_stale` (WARNING): worker liveness, the structured-log substitute for a scrape gauge (no Prometheus worker series ships).
- `workers.seen_claims_pruner.pruned` (INFO): count of expired dedup rows reclaimed.

### External inspection

Once NATS is running, operators can inspect bus state without a Python interpreter:

- `nats stream ls`: list streams, including `SYNTHORG_BUS` and (if Phase 4 is running) `SYNTHORG_TASKS`
- `nats stream info SYNTHORG_BUS`: message counts, subject cardinality, retention policy
- `nats consumer ls SYNTHORG_BUS`: per-(channel, subscriber) durable consumers with pending / delivered / ack-pending counts
- `nats sub 'synthorg.bus.channel.>'`: tail messages across all bus channels in real time
- `nats consumer report SYNTHORG_TASKS`: per-consumer pending / ack-pending for the work queue and its dead-letter consumer (the backpressure + dead-letter view)
- `nats sub 'synthorg.workers.heartbeat.>'`: tail worker liveness beats directly (the heartbeat is core NATS, not JetStream, so it does not appear under `nats stream`)
- `docker compose exec nats wget -qO- localhost:8222/varz`: NATS monitoring HTTP endpoint, exposes Prometheus-compatible metrics (not mapped to a host port by default; use a compose override if external access is needed)

---

## Open Questions

Points to resolve during Phase 1 review. Each becomes a decision the Phase 2 implementation commits to.

- **Dynamic channel registry source of truth.** JetStream KV bucket vs eager channel creation from config vs a distributed consensus store. Recommendation: KV bucket + config bootstrap, as described in "Channel registry" above. KV is already a JetStream primitive, no extra infrastructure.
- **Should `publish()` wait for server ack?** `nats-py` exposes fire-and-forget and ack-waiting publish variants. In-memory is synchronous so a caller knows the message reached the queue before `publish()` returns. Recommendation: ack-waiting publish in the default code path, with a config knob to downgrade to fire-and-forget for latency-sensitive deployments. Adds ~1 ms per publish, preserves semantics.
- **`NotSubscribedError` semantics for DIRECT channels.** In-memory raises `NotSubscribedError` for a non-subscriber attempting `receive()` on a TOPIC or DIRECT channel. On JetStream, a durable consumer only exists if the subscriber has called `subscribe()`, so the error condition is naturally enforced. Recommendation: mirror the in-memory check by tracking subscription state in-process and raising `NotSubscribedError` before calling `fetch()`. Same exception, same condition.
- **Testcontainer strategy for conformance tests.** Phase 2 extracts the existing bus tests into a shared contract and runs them against both backends. The NATS runs need a real server. Recommendation: `testcontainers-python` with the `nats` image (the canonical tag pin lives in `tests/integration/communication/test_bus_nats.py` and is kept current by Renovate's testcontainers customManager), marked `@pytest.mark.integration`, skipped in unit runs. Works cross-platform (Windows laptop dev + Linux CI).
- **CLI picker fallback when TTY is absent.** Non-interactive contexts (CI, scripts, `--yes`, `SYNTHORG_YES=1`) cannot render the picker. Recommendation: honor `--yes` and `--bus-backend` flags first; if neither is set and no TTY is attached, default to `internal` silently. Invalid explicit values exit 2.

---

## Related Reading

- [Communication](communication.md): `MessageBus` protocol, message format, channel types
- [Engine](engine.md): `TaskEngine` single-writer mutation queue, task lifecycle
- [Deployment](deployment.md): container runtime, image verification
- [Observability](observability.md): structured logging, correlation, sinks
- [Notifications](notifications.md): notification dispatcher and sinks
- [Architecture: Tech Stack](../architecture/tech-stack.md): Message Bus row in the stack table
- [Roadmap: Scaling Path](../roadmap/future-vision.md#scaling-path): Phase 2 Local Multi-Process constraints
- [Issue #236](https://github.com/Aureliolo/synthorg/issues/236): distributed/persistent message bus backend
- [Issue #237](https://github.com/Aureliolo/synthorg/issues/237): distributed task queue
