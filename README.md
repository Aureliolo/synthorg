# AI Company

[![CI](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml/badge.svg)](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml)

A framework for orchestrating autonomous AI agents as employees within a virtual company structure.

## Concept

AI Company lets you spin up a virtual organization staffed entirely by AI agents. Each agent has a role (CEO, developer, designer, QA, etc.), a personality, persistent memory, and access to real tools. Agents collaborate through structured communication, follow workflows, and produce real artifacts - code, documents, designs, and more.

## Current Capability Snapshot

### Implemented (M0–M6 complete, M7 security + HR partial)

- **Company Config + Core Models** - Strong Pydantic validation, immutable config models, runtime state models
- **Provider Layer** - LiteLLM-based provider abstraction with routing, retry, and rate limiting
- **Budget Tracking** - Cost records, summaries, and coordination analytics models
- **Tool System** - File system tools, git tools, sandbox abstraction (subprocess + Docker), code runner, MCP bridge, permission gating
- **Single-Agent Engine (M3)** - ReAct/Plan-Execute loops, fail-and-reassign recovery, graceful shutdown
- **Multi-Agent Core (M4)** - Message bus, delegation with loop prevention, conflict resolution, meeting protocols
- **Task Intelligence (M4)** - Task decomposition, routing, assignment strategies, workspace isolation via git worktrees
- **Templates** - Built-in templates, inheritance/merge, rendering, personality presets
- **Persistence Layer (M5)** - Pluggable `PersistenceBackend` protocol with SQLite backend (aiosqlite), repository protocols, schema migrations
- **Memory Interface (M5)** - Pluggable `MemoryBackend` protocol with capability discovery, shared knowledge protocol, domain models, config, factory, and context injection retrieval pipeline (ranking, token-budget formatting, non-inferable filtering). Shared organizational memory via `OrgMemoryBackend` protocol with hybrid prompt+retrieval backend. Memory consolidation/archival with pluggable strategies and retention enforcement
- **Coordination Error Taxonomy (M5)** - Post-execution classification pipeline detecting logical contradictions, numerical drift, context omissions, and coordination failures
- **Budget Enforcement (M5)** - `BudgetEnforcer` service with pre-flight checks, in-flight budget checking, auto-downgrade, configurable cost tiers, and quota/subscription tracking; `CostOptimizer` CFO service with anomaly detection, efficiency analysis, downgrade recommendations, and approval decisions; `ReportGenerator` for multi-dimensional spending reports
- **Litestar REST API (M6)** - 15 controllers + WebSocket handler covering company, agents, tasks, budget, approvals, analytics, messages, meetings, projects, departments, artifacts, providers, health, auth, and WebSocket real-time feed
- **Human Approval Queue (M6)** - Approval submission, approve/reject with reason, list/filter by status, WebSocket notifications for approval events
- **WebSocket Real-Time Feed (M6)** - Channel-based subscriptions (tasks, agents, budget, messages, system, approvals), per-channel payload filters, message-bus bridge
- **Route Guards (M6)** - Role-based read/write access control with 5 human roles (CEO, Manager, Board Member, Pair Programmer, Observer)
- **JWT + API Key Authentication (M7)** - Mandatory auth middleware (JWT-first with API key fallback), Argon2id password hashing, first-run admin setup, password change flow, SHA-256 API key hashing, regex-based path exclusions
- **HR Engine (M7)** - Hiring pipeline (request → generate candidate → approval → instantiate), onboarding checklists, offboarding pipeline (reassign → archive → notify → terminate), agent registry
- **Performance Tracking (M7)** - Task metrics, CI-based quality scoring, behavioral collaboration scoring, Theil-Sen robust trend detection, multi-window rolling metric aggregation
- **Progressive Trust (M7)** - 4 strategies (disabled/weighted/per-category/milestone) behind pluggable `TrustStrategy` protocol, trust level tracking, action permission evaluation
- **Promotion/Demotion (M7)** - Criteria evaluation (ThresholdEvaluator), approval strategies (SeniorityApprovalStrategy), model mapping (SeniorityModelMapping), PromotionService orchestrator
- **Security Subsystem (M7)** - SecOps agent with rule engine (soft-allow/hard-deny, fail-closed), audit log, output scanner, output scan response policies (redact/withhold/log-only/autonomy-tiered), risk classifier, ToolInvoker integration, autonomy levels (5 tiers with presets, resolver, change strategies), approval timeout policies (wait-forever/auto-deny/tiered/escalation-chain with task park/resume)

### Not implemented yet (planned milestones)

- **Memory Backend Adapter (M5)** - Memory protocols, retrieval pipeline, org memory, and consolidation are complete; initial Mem0 adapter backend ([ADR-001](docs/decisions/ADR-001-memory-layer.md)) pending; research backends (GraphRAG, Temporal KG) planned
- **CLI Surface** - `cli/` package is placeholder-only
- **Security/Approval System (M7)** - SecOps agent with rule engine (soft-allow/hard-deny, fail-closed), audit log, output scanner, risk classifier, and ToolInvoker integration are implemented; progressive trust (4 strategies), promotion/demotion, autonomy levels (5 tiers with presets, resolver, change strategies) and approval timeout policies (wait-forever, auto-deny, tiered, escalation-chain with task park/resume) are implemented; JWT + API key authentication is implemented; approval workflow gates remain planned
- **Advanced Product Surface** - web dashboard, external integrations

## Status

**M7: Security & Approval** partially complete — Docker sandbox, MCP bridge, code runner, SecOps agent, HR engine + performance tracking, progressive trust, promotion/demotion, JWT + API key authentication done; approval workflow gates remain. See [DESIGN_SPEC.md](DESIGN_SPEC.md) for the full high-level specification.

## Tech Stack

- **Python 3.14+** with Litestar, Pydantic
- **uv** as package manager, **Hatchling** as build backend
- **LiteLLM** for multi-provider LLM abstraction
- **structlog** for structured logging and observability
- **Mem0** for agent memory (initial backend; custom stack future — see [ADR-001](docs/decisions/ADR-001-memory-layer.md))
- **MCP** for tool integration
- **Vue 3** for web dashboard (planned)
- **SQLite** (aiosqlite) → PostgreSQL for operational data persistence
- **Docker** with Chainguard Python distroless runtime (CIS-hardened, non-root)
- **nginx** (unprivileged) for web UI reverse proxy

## System Requirements

- **Python 3.14+**
- **uv** — package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **Git 2.x+** — required at runtime for built-in git tools (subprocess-based, not a Python binding)
- **Docker** (optional) — required for code execution sandbox, Docker-backed tool isolation, and running the full stack via Docker Compose. Install [Docker Desktop](https://docs.docker.com/get-docker/) or Docker Engine. File system and git tools work without Docker via subprocess isolation.

## Getting Started

### Development (local Python)

```bash
git clone https://github.com/Aureliolo/ai-company.git
cd ai-company
uv sync
```

See [docs/getting_started.md](docs/getting_started.md) for prerequisites, IDE setup, and the full walkthrough.

### Docker Compose (full stack)

```bash
cp docker/.env.example docker/.env   # configure env vars (set LLM_API_KEY)
docker compose -f docker/compose.yml build
docker compose -f docker/compose.yml up -d
```

Services (default ports, configurable via `BACKEND_PORT` / `WEB_PORT` in `docker/.env`):
- **Backend API**: `http://localhost:8000` — Litestar REST + WebSocket
- **Web Dashboard**: `http://localhost:3000` — placeholder (proxies `/api/` and `/ws` to backend)

```bash
curl http://localhost:8000/api/v1/health   # health check (default port)
docker compose -f docker/compose.yml down  # stop services
```

See [docker/](docker/) for Dockerfiles, compose config, and environment variable reference.

## Documentation

- [Getting Started](docs/getting_started.md) - Setup and installation guide
- [Contributing](.github/CONTRIBUTING.md) - Branch, commit, and PR workflow
- [CLAUDE.md](CLAUDE.md) - Code conventions and AI assistant reference
- [Design Specification](DESIGN_SPEC.md) - Full high-level design

## License

[Business Source License 1.1](LICENSE) — converts to Apache 2.0 on 2030-02-27.
