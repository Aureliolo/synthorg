# Roadmap

## Current status

SynthOrg is in **active development**. The platform, infrastructure, and
subsystem libraries are built and tested (<!--RS:tests-->33,000+<!--/RS-->
tests in the latest run, 80%+ coverage) and integrated through a REST +
WebSocket API, a React 19 dashboard, and a Go CLI. The autonomous agent
**runtime** that makes the organisation actually execute work is the focus of
current development and is tracked openly on the
[issue tracker](https://github.com/Aureliolo/synthorg/issues). Be specific
about what this means: starting SynthOrg brings up the platform and
dashboard; running a company end to end is the work in flight.

## Available now

Shipped and exercised today:

- **API, dashboard, CLI**: REST + WebSocket API, the React 19 dashboard, and
  the Go CLI for Docker orchestration and supply-chain verification.
- **Persistence**: SQLite (single-node default) and PostgreSQL
  (multi-instance), dual-backend conformance-tested, with in-process
  yoyo-managed migrations and ISO 4217 currency stamping on every
  cost-bearing row.
- **Provider layer**: any LLM via LiteLLM with retry and rate-limit handling;
  local model management for Ollama and LM Studio.
- **Configuration and templates**: define a company in YAML; importable
  agent, department, and company templates with personality presets and
  locale-aware name generation.
- **Operations**: structured logging with correlation tracking and
  redaction, log shipping, Prometheus metrics, OTLP.
- **Multi-user access**: HttpOnly cookie sessions, CSRF protection,
  concurrent session control, JWT auth.
- **Supply chain**: Chainguard distroless images, Trivy + Grype scanning,
  cosign signatures, SLSA L3 provenance.
- **Distributed dispatch plumbing**: the NATS JetStream queue and worker
  pool exist; the task-execute endpoint currently advances task state and
  does not yet invoke an agent.
- **Subsystem libraries**: the engine, memory, budget, security,
  coordination, and intake modules exist as importable, unit-tested
  components. They are exercised by their test suites, not yet by a running
  agent (see below).

## In active development

These make SynthOrg an autonomous studio. They are designed and largely
written as components, but not yet wired into a running product. Each is
tracked as an epic:

- **Agent runtime online**
  ([EPIC #1955](https://github.com/Aureliolo/synthorg/issues/1955)):
  agents actually executing tasks (LLM + sandboxed tools) under a minimal
  safety spine. The foundational item; everything else depends on it. Until
  it lands, multi-agent coordination, coordination metrics, the intake
  engine, the approval-queue producer, autonomy/trust enforcement on a live
  run, and the self-improvement loop are implemented and unit-tested but not
  exercised end to end.
- **Conversational org interface**
  ([EPIC #1967](https://github.com/Aureliolo/synthorg/issues/1967)): talk to
  the company in natural language; it clarifies, proposes, and later acts
  under governance.
- **Autonomous product studio substrate**
  ([EPIC #1973](https://github.com/Aureliolo/synthorg/issues/1973)):
  persistent project workspace with pluggable git, brownfield codebase
  intake, living documentation, and a deep requirements interview.
- **Best-in-class operate tier**
  ([EPIC #1979](https://github.com/Aureliolo/synthorg/issues/1979)): a
  golden-company benchmark, mission control with run replay, a cost
  forecast/kill-switch dial, a measurable learning curve, deterministic
  replay, run narratives, and an adversarial red-team.
- **Agent capability layer**
  ([EPIC #1987](https://github.com/Aureliolo/synthorg/issues/1987)): a
  knowledge and provenance retrieval substrate, research mode, continual
  improvement, governed external API access, headless-browser and
  virtual-desktop testing, and more.

## Backlog

Research candidates and longer-term ideas without a scheduled timeframe. See
[Future Vision](future-vision.md) for detail.

- Advanced memory architecture (GraphRAG, consistency protocols, RL
  consolidation)
- Community template marketplace
- Kubernetes sandbox backend
- Shift system for agents
- Training mode (learn from senior agents)
- TimescaleDB hypertable support for append-only time-series tables

See [Open Questions](open-questions.md) for unresolved design decisions.
