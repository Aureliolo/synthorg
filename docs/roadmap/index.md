# Roadmap

## Current status

SynthOrg is **pre-alpha**. The platform, infrastructure, and runtime are built
and tested (<!--RS:tests-->44,000+<!--/RS--> tests in the most recent run) and
integrated through a REST + WebSocket API, a React 19 dashboard,
and a Go CLI. The agent runtime, multi-agent coordinator, work pipeline spine,
intake engine, sandbox lifecycle dispatch, and distributed-path consumers are
all wired and exercised by deterministic e2e harnesses with a scripted provider
(no real LLM spend). What remains in flight is the operator-facing maturity that
turns a wired runtime into something you would leave running, plus real-provider
acceptance against a live LLM. Progress is tracked openly on the
[issue tracker](https://github.com/Aureliolo/synthorg/issues).

## Available now

Shipped and exercised today (by deterministic e2e harnesses with a scripted
provider, zero LLM spend, unless noted):

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
  agent, department, and company templates with personality presets and
  locale-aware name generation.
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
  behind `simulations.client_intake_enabled`.
- **Sandbox lifecycle dispatch**: `DockerSandbox.execute()` honours `owner_id`
  and dispatches to the configured per-call / per-agent / per-task lifecycle
  strategy, with grace-period teardown.
- **Distributed dispatch**: NATS JetStream queue, worker pool, dead-letter
  consumer, dedup pruner, and heartbeat subscriber, validated under
  multi-worker synthetic load (no loss, no duplication).
- **Conversational org interface**: one unified "talk to your org" chat in
  natural language. A single turn is classified to an intent (answer a question,
  draft a plan, convene a group, act) and dispatched, with per-turn concern
  routing to the closest-fit role agent and transparent multi-voice (specialists
  chime in with attribution). Human-consented agent-initiated invites and direct
  MCP acting under trust (sensitive actions approval-gated; fail-closed when
  security governance is inactive) round it out. The read/propose/group and
  multi-voice capabilities are on by default and gate live per request;
  agent-initiated invites and direct MCP acting are off by default.
- **Operations**: structured logging with correlation tracking and redaction,
  log shipping, Prometheus metrics, OTLP, HttpOnly-cookie multi-user sessions
  with CSRF protection, Wolfi apko-composed distroless images, Trivy + Grype
  scanning, cosign signatures, and SLSA L3 provenance.
- **Product studio substrate**: persistent project workspace with pluggable
  git, brownfield codebase intake, living documentation, and a deep
  requirements interview.
- **Operate tier**: golden-company benchmark, mission control with run replay,
  a cost forecast/kill-switch dial, a measurable learning curve, deterministic
  replay, run narratives, and an adversarial red-team.
- **Agent capability layer**: a knowledge and provenance retrieval substrate,
  research mode, continual improvement, governed external API access, and
  headless-browser and virtual-desktop testing.

## In active development

These turn a wired runtime into something you would leave running. The runtime,
coordinator, intake, work pipeline, sandbox dispatch, and distributed-path
consumers already run under deterministic harnesses; what remains is
operator-facing maturity and real-provider acceptance:

- **Self-improvement loop**: company-wide signals from existing subsystems
  producing deployment and product-level improvement proposals through a
  rule-first hybrid pipeline with mandatory human approval. Components built
  and unit-tested; live end-to-end run pending.
- **Real-provider acceptance**: the e2e harness drives the runtime against a
  deterministic scripted provider, not a real LLM. A real-provider
  golden-company benchmark and run narrative arrive with the operate tier.

## Backlog

Research candidates and longer-term ideas without a scheduled timeframe. See
[Future Vision](future-vision.md) for detail.

- Advanced memory architecture (GraphRAG, RL consolidation)
- Distributed multi-node organisational memory consistency (Phase 2
  compare-and-set on PostgreSQL advisory locks)
- A2A skill negotiation and inter-org federation (delegation across
  organisations)
- Community template marketplace
- Kubernetes sandbox backend
- Shift system for agents
- Training mode (learn from senior agents)

See [Open Questions](open-questions.md) for unresolved design decisions.
