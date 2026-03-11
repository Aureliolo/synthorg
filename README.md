# SynthOrg

[![CI](https://github.com/Aureliolo/synthorg/actions/workflows/ci.yml/badge.svg)](https://github.com/Aureliolo/synthorg/actions/workflows/ci.yml)

A framework for building synthetic organizations — autonomous AI agents orchestrated as a virtual company.

## Concept

SynthOrg lets you spin up a synthetic organization staffed entirely by AI agents. Each agent has a role (CEO, developer, designer, QA, etc.), a personality, persistent memory, and access to real tools. Agents collaborate through structured communication, follow workflows, and produce real artifacts - code, documents, designs, and more.

## What's Built

### Core Framework

- Company config + core models — Pydantic validation, immutable config, runtime state
- Provider layer — LiteLLM-based abstraction with routing, retry, rate limiting
- Templates — built-in templates, inheritance/merge, personality presets
- Persistence — pluggable `PersistenceBackend` protocol, SQLite backend, schema migrations

### Agent Engine

- Single-agent execution — ReAct/Plan-Execute loops, fail-and-reassign recovery, graceful shutdown
- Multi-agent orchestration — message bus, delegation, loop prevention, conflict resolution, meeting protocols
- Task intelligence — decomposition, routing, assignment strategies, workspace isolation (git worktrees)
- Coordination error taxonomy — post-execution classification (contradictions, drift, omissions)

### Communication

- Message bus with dispatcher and channels
- Delegation with loop prevention
- Conflict resolution (4 strategies: authority+dissent, debate+judge, human escalation, hybrid)
- Meeting protocols (round-robin, position papers, structured phases)

### Budget & Cost Management

- Cost tracking — records, summaries, coordination analytics
- Budget enforcement — pre-flight/in-flight checks, auto-downgrade, cost tiers, quota tracking
- CFO optimization — anomaly detection, efficiency analysis, downgrade recommendations, spending reports

### Memory

- Pluggable `MemoryBackend` protocol — capability discovery, retrieval pipeline (ranking, formatting, filtering)
- Shared org memory — `OrgMemoryBackend` with hybrid prompt+retrieval backend
- Consolidation/archival — pluggable strategies, retention enforcement

### Tool System

- Built-in tools — file system, git, code runner
- Sandboxing — subprocess (file/git) + Docker (code execution)
- MCP bridge — Model Context Protocol integration
- Permission gating — role-based access, category-level enforcement

### API & Human Interaction

- REST API — Litestar, 15 controllers (company, agents, tasks, budget, approvals, analytics, messages, meetings, projects, departments, artifacts, providers, health, auth)
- WebSocket — channel-based subscriptions, per-channel filters, message-bus bridge
- Approval queue — submit/approve/reject, status filtering, WebSocket notifications
- Route guards — role-based access control, 5 human roles

### Security

- Authentication — JWT + API key, Argon2id hashing, first-run admin setup
- SecOps agent — rule engine (soft-allow/hard-deny, fail-closed), audit log, output scanner, risk classifier
- Progressive trust — 4 strategies behind `TrustStrategy` protocol
- Autonomy levels — 5 tiers, presets, resolver, change strategies
- Approval timeout policies — wait-forever/auto-deny/tiered/escalation-chain, task park/resume

### HR

- Hiring pipeline — request, candidate generation, approval, instantiation
- Onboarding checklists, offboarding pipeline (reassign, archive, notify, terminate)
- Agent registry
- Performance tracking — task metrics, quality scoring, collaboration scoring, trend detection
- Promotion/demotion — criteria evaluation, approval strategies, model mapping

### Planned

- Memory backend adapter — Mem0 initial ([ADR-001](docs/decisions/ADR-001-memory-layer.md)); GraphRAG, Temporal KG on roadmap
- Approval workflow gates — integration with engine execution flow
- CLI surface — `cli/` package is placeholder-only
- Web dashboard — Vue 3 (planned)

## Status

Core framework complete — agent engine, multi-agent coordination, API, security, HR, memory, and budget systems are implemented. Remaining: Mem0 adapter backend, approval workflow gates, CLI, web dashboard. See [DESIGN_SPEC.md](DESIGN_SPEC.md) for the full specification.

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
git clone https://github.com/Aureliolo/synthorg.git
cd synthorg
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
