# Roadmap

## Current Status

SynthOrg is in **active development**. The core subsystems are built, tested (26,900+ tests in the latest run on 2026-05-05, 80%+ coverage), and integrated through a REST + WebSocket API, React 19 dashboard, and Go CLI. See the [releases page](https://github.com/Aureliolo/synthorg/releases) for the latest tagged build.

What works today:

- **Agent engine** with ReAct, Plan-and-Execute, Hybrid execution loops, crash recovery, task decomposition, and agent identity versioning (list, diff, rollback, append-only history)
- **Agent evolution** with pluggable triggers (batched, inflection, per-task), proposers (separate-analyzer, self-report, composite), and guards (rollback, review, rate limit, shadow evaluation)
- **Dynamic workforce scaling** with closed-loop hiring and pruning, safety guards, and approval gates
- **Budget & cost management** with per-agent limits, auto-downgrade at task boundaries, spending reports, anomaly detection, and hierarchical project cascades
- **Security** with fail-closed rule engine, 4 autonomy tiers, progressive trust, output scanning, audit logging, two-stage safety classifier, and hallucination detection via cross-provider uncertainty check
- **Memory** with hybrid retrieval (dense + BM25 sparse with RRF fusion), tool-based injection, procedural memory auto-generation from failures, consolidation (LLM Merge, Search-and-Ask), and MVCC snapshot reads on the shared knowledge store
- **Communication** with message bus, hierarchical delegation with loop prevention, conflict resolution (4 strategies), meeting protocols, and an A2A gateway for external agent systems
- **Workflow engine** with Kanban, Agile sprints, ceremony scheduling (8 strategies), visual workflow editor, and workflow execution from graph definitions
- **Tool ecosystem** with 8+ categories (file system, git, web, database, terminal, sandbox, MCP bridge, analytics, communication) and sandbox security (auth proxy, gVisor, Chainguard packages)
- **Persistence** with SQLite (single-node default) and PostgreSQL (multi-instance, dual-backend conformance-tested) backends, Atlas-managed schema migrations, and ISO 4217 currency stamping on every cost-bearing row
- **Distributed runtime** with NATS JetStream message bus and distributed task queue for multi-instance deployments
- **Web dashboard** (React 19 + shadcn/ui) with org chart, task board, agent detail, budget tracking, provider management, workflow editor, ceremony policy settings, setup wizard, and WebSocket / SSE resilience
- **CLI** (Go) with init, start, stop, doctor, config, wipe, cleanup, worker, backup, completion, and cosign / SLSA verification
- **Docker deployment** with Chainguard distroless images, Trivy + Grype scanning, cosign signatures, and SLSA L3 provenance
- **Multi-user access** with HttpOnly cookie sessions, CSRF protection, concurrent session control, JWT auth, and session management
- **Local model management** for Ollama and LM Studio (browse, pull, delete, configure launch parameters)
- **Observability** with structured logging, correlation tracking, log shipping, redaction, Prometheus metrics, and OTLP

What's not there yet:

- **End-to-end production runs**: subsystems are integrated but the full autonomous loop (agents receiving work, executing, producing artifacts, iterating) has not been validated as a cohesive product
- **Runtime configuration surface for agent evolution**: the evolution service is implemented but wired at app init; runtime configuration via REST or UI is not yet exposed

## Planned

Prioritised by dependency order. All work is tracked on the [GitHub issue tracker](https://github.com/Aureliolo/synthorg/issues).

- Operational guides: runtime settings reference, notifications and event subscriptions, workflow API tutorials, agent lifecycle, memory admin API
- Notification sink MVP for operator alerts (Slack, ntfy, email via HTTP sink relay)
- OpenAPI TypeScript codegen for the web dashboard
- REST API and dashboard UI for agent evolution configuration and triggering
- PostgreSQL performance tuning + TimescaleDB hypertable support for append-only time-series tables
- Dynamic company scaling across clusters
- Multi-project support with project-scoped teams and isolated budgets
- Plugin system and benchmarking suite
- A2A protocol compatibility: finalise inter-org communication surface

## Backlog

Research candidates and longer-term ideas without a scheduled timeframe. See [Future Vision](future-vision.md) for detail.

- Advanced memory architecture (GraphRAG, consistency protocols, RL consolidation)
- Community template marketplace
- Kubernetes sandbox backend
- Shift system for agents
- Training mode (learn from senior agents)
- Self-improving company (meta-loop signal aggregation, staged rollout)

See [Open Questions](open-questions.md) for unresolved design decisions.
