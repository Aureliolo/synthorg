# Roadmap

## Status

SynthOrg is **pre-alpha**, and the plain summary is that the loop does not yet
complete.

The platform underneath it is built and tested: <!--RS:tests-->46,000+<!--/RS-->
tests in the most recent run, a REST + WebSocket API, a React 19 dashboard, a Go
CLI, and deterministic end-to-end harnesses that drive the agent runtime, the
multi-agent coordinator, the work pipeline spine, the intake engine, sandbox
lifecycle dispatch, and the distributed-path consumers against a scripted
provider with no LLM spend. Those harnesses establish that the wiring holds.
They do not establish that the loop delivers, because a scripted provider does
no decomposition.

Against a real deployment with real models, the loop has been driven end to end
twelve times and has never reached the assembly stage. No run has produced an
assembled deliverable, and no completion has been recorded. Rounds stopped on
authentication; a Windows event-loop split that left the runtime with no agent
tools; a reasoning model answering on a channel the loop did not read; a
provider outage; a completion review reading its deliverable from a store
written after the review had already ruled; a replan generation cap with no
exit; a plan repair that could not converge; and three separate decomposition
bounds that each discarded a tree which had already converged.

The stop point has moved downstream. The early rounds died on the deployment,
the middle rounds died in planning and review, and the most recent died inside
recursive decomposition, having built a tree to depth four with subtrees of 20,
15, 12, and 11 leaves, and having absorbed live the two bounds that had been
fatal one round earlier. It stopped because the two caps that size a level
contradict each other: the planner was ordered to widen, then failed for
widening.

That is the state to plan against. The machinery runs, the tree builds, and
nothing has yet come out of the far end. Every round is recorded in the
[loop round log](../reference/loop-round-log.md), and work is tracked openly on
the [issue tracker](https://github.com/Aureliolo/synthorg/issues).

## Built and exercised

Present in the product and covered by deterministic end-to-end harnesses with a
scripted provider (no LLM spend) unless noted. Coverage under harness is not
evidence that the loop completes; see [Status](#status).

- **API, dashboard, CLI**: REST + WebSocket API, the React 19 dashboard, and
  the Go CLI for Docker orchestration and supply-chain verification.
- **Persistence**: SQLite (single-node default) and PostgreSQL
  (multi-instance), dual-backend conformance-tested, with in-process
  yoyo-managed migrations and ISO 4217 currency stamping on every
  cost-bearing row. Opt-in TimescaleDB hypertable conversion for the
  append-only `cost_records` and `audit_entries` tables
  (`enable_timescaledb: false` by default; ignored gracefully when the
  extension is absent).
- **Provider layer**: any LLM via LiteLLM with retry and rate-limit handling;
  local model management for Ollama and LM Studio.
- **Configuration and templates**: define a company in YAML; importable
  agent, department, and company templates with locale-aware name generation.
- **Agent runtime**: a configured provider boots a real agent runtime that
  executes tasks (LLM + sandboxed tools) under a minimal safety spine
  (autonomy/trust verdict on tool actions, approval-queue producer for
  sensitive actions). An empty company (no provider) cleanly rejects task
  submission.
- **Multi-agent coordinator and work pipeline spine**: `/coordinate` runs
  decompose, route, parallel execution, then roll up end-to-end behind the
  provider-present switch. The shared work pipeline (intake to projects to
  decompose to solo/team to execute to coordination metrics) is the single
  integration point every entry adapter feeds, with solo-vs-team decided
  internally by decomposition.
- **Entry adapters**: work-entry paths into the pipeline spine. Stated
  objectives (`POST /objectives`) are the always-on operator door, each
  standing up its own per-initiative project; the task board (`POST /tasks`)
  files against a caller-named project; the synthetic-client intake door
  (`POST /requests/{id}/approve`) is a benchmark surface, off by default
  behind `simulations.client_intake_enabled`. Standing up a full initiative
  (a brief the spine must decompose into a plan) has exactly one path, and it
  ends at an operator approving a charter: `WorkItem.charter_id` is refused at
  the type level unless the approval that authorised the commitment is
  attached.
- **Sandbox lifecycle dispatch**: `DockerSandbox.execute()` honours `owner_id`
  and dispatches to the configured per-call / per-agent / per-task lifecycle
  strategy, with grace-period teardown.
- **Distributed dispatch**: NATS JetStream queue, worker pool, dead-letter
  consumer, dedup pruner, and heartbeat subscriber, validated under
  multi-worker synthetic load (no loss, no duplication).
- **Conversational interface**: one unified chat with the running system, in
  natural language. A single turn is classified to an intent (answer a question,
  steer work a charter already authorised, convene a group, act, run a charter
  interview, configure the control plane) and dispatched, with transparent
  multi-voice so specialists chime in with attribution. The read, propose,
  group and multi-voice capabilities are on by default and gate live per
  request. Per-turn concern routing to the closest-fit role agent needs both
  `chief_of_staff.routing_enabled` and the persona master switch
  `self_improvement.chief_of_staff_enabled`, and because the master ships off,
  routing is off until an operator turns it on. Agent-initiated invites, direct
  MCP acting and the operator console are off by default as well, and the
  latter two fail closed when security governance is inactive.
- **Operations**: structured logging with correlation tracking and redaction,
  log shipping, Prometheus metrics, OTLP, HttpOnly-cookie multi-user sessions
  with CSRF protection, Wolfi apko-composed distroless images, Trivy
  scanning, cosign signatures, and SLSA L3 provenance.
- **Delivery substrate**: persistent project workspace with pluggable git,
  brownfield codebase intake, living documentation, and a deep requirements
  interview.
- **Operate tier**: golden-company benchmark, mission control with run replay,
  a cost forecast/kill-switch dial, a measurable learning curve, deterministic
  replay, run narratives, and an adversarial red-team.
- **Agent capability layer**: a knowledge and provenance retrieval substrate,
  research mode, continual improvement, governed external API access, and
  headless-browser and virtual-desktop testing.

## In active development

The work that stands between the machinery above and a loop that finishes:

- **Getting a run to the assembly stage**: each live round is scoped to move the
  stop point downstream and to record where it lands. The open work is the
  decomposition bounds the most recent rounds died on, chiefly the two caps
  that size a level and contradict each other, plus the stall detection that
  did not fire on a planning session repeating one fruitless tool call until
  its turn budget ran out.
- **Self-improvement loop**: company-wide signals from existing subsystems
  producing deployment and product-level improvement proposals through a
  rule-first hybrid pipeline with mandatory human approval. Components built
  and unit-tested; the master switch `self_improvement.enabled` ships off and
  no live end-to-end run has happened.
- **Real-provider acceptance**: the end-to-end harness drives the runtime
  against a deterministic scripted provider rather than a real LLM. A
  real-provider golden-company benchmark and run narrative arrive with the
  operate tier.

## Backlog

Research candidates and longer-term ideas without a scheduled timeframe. See
[Future Vision](future-vision.md) for detail.

- Advanced memory architecture (GraphRAG, RL consolidation)
- Distributed multi-node organisational memory consistency (Phase 2
  compare-and-set on PostgreSQL advisory locks)
- Inter-org federation as an operator surface. The A2A gateway, the peer
  registry and the five JSON-RPC methods (including `skills/query` and
  `skills/negotiate`) are implemented and covered by in-process tests, but no
  harness stands two deployments up against each other, so delegation across
  organisations is unexercised
- Community template marketplace
- Kubernetes sandbox backend
- Shift system for agents
- Training mode (learn from senior agents)

See [Open Questions](open-questions.md) for unresolved design decisions.
