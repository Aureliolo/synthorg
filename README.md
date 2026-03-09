# AI Company

[![CI](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml/badge.svg)](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml)

A framework for orchestrating autonomous AI agents as employees within a virtual company structure.

## Concept

AI Company lets you spin up a virtual organization staffed entirely by AI agents. Each agent has a role (CEO, developer, designer, QA, etc.), a personality, persistent memory, and access to real tools. Agents collaborate through structured communication, follow workflows, and produce real artifacts - code, documents, designs, and more.

## Current Capability Snapshot

### Implemented (M0–M6 complete)

- **Company Config + Core Models** - Strong Pydantic validation, immutable config models, runtime state models
- **Provider Layer** - LiteLLM-based provider abstraction with routing, retry, and rate limiting
- **Budget Tracking** - Cost records, summaries, and coordination analytics models
- **Tool System** - File system tools, git tools, sandbox abstraction, permission gating
- **Single-Agent Engine (M3)** - ReAct/Plan-Execute loops, fail-and-reassign recovery, graceful shutdown
- **Multi-Agent Core (M4)** - Message bus, delegation with loop prevention, conflict resolution, meeting protocols
- **Task Intelligence (M4)** - Task decomposition, routing, assignment strategies, workspace isolation via git worktrees
- **Templates** - Built-in templates, inheritance/merge, rendering, personality presets
- **Persistence Layer (M5)** - Pluggable `PersistenceBackend` protocol with SQLite backend (aiosqlite), repository protocols, schema migrations
- **Memory Interface (M5)** - Pluggable `MemoryBackend` protocol with capability discovery, shared knowledge protocol, domain models, config, factory, and context injection retrieval pipeline (ranking, token-budget formatting). Shared organizational memory via `OrgMemoryBackend` protocol with hybrid prompt+retrieval backend. Memory consolidation/archival with pluggable strategies and retention enforcement
- **Coordination Error Taxonomy (M5)** - Post-execution classification pipeline detecting logical contradictions, numerical drift, context omissions, and coordination failures
- **Budget Enforcement (M5)** - `BudgetEnforcer` service with pre-flight checks, in-flight budget checking, auto-downgrade, configurable cost tiers, and quota/subscription tracking; `CostOptimizer` CFO service with anomaly detection, efficiency analysis, downgrade recommendations, and approval decisions; `ReportGenerator` for multi-dimensional spending reports
- **Litestar REST API (M6)** - 14 controllers covering company, agents, tasks, budget, approvals, analytics, messages, meetings, projects, departments, artifacts, providers, health, and WebSocket real-time feed
- **Human Approval Queue (M6)** - Approval submission, approve/reject with reason, list/filter by status, WebSocket notifications for approval events
- **WebSocket Real-Time Feed (M6)** - Channel-based subscriptions (tasks, agents, budget, messages, system, approvals), per-channel payload filters, message-bus bridge
- **Route Guards (M6)** - Role-based read/write access control (stub auth for M6; real JWT/OAuth planned for M7)

### Not implemented yet (planned milestones)

- **Memory Backends (M5)** - Initial Mem0 adapter backend ([ADR-001](docs/decisions/ADR-001-memory-layer.md)) pending; research backends (GraphRAG, Temporal KG) planned
- **CLI Surface** - `cli/` package is placeholder-only
- **Security/Approval System (M7)** - `security/` package is placeholder-only; real authentication (JWT/OAuth), progressive trust, SecOps agent
- **Advanced Product Surface** - web dashboard, HR workflows, and external integrations

## Status

**M7: Security & HR** next (M0–M6 all done). See [DESIGN_SPEC.md](DESIGN_SPEC.md) for the full high-level specification.

## Tech Stack

- **Python 3.14+** with Litestar, Pydantic
- **uv** as package manager, **Hatchling** as build backend
- **LiteLLM** for multi-provider LLM abstraction
- **structlog** for structured logging and observability
- **Mem0** for agent memory (initial backend; custom stack future — see [ADR-001](docs/decisions/ADR-001-memory-layer.md))
- **MCP** for tool integration (planned)
- **Vue 3** for web dashboard (planned)
- **SQLite** (aiosqlite) → PostgreSQL for operational data persistence

## System Requirements

- **Python 3.14+**
- **uv** — package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **Git 2.x+** — required at runtime for built-in git tools (subprocess-based, not a Python binding)

## Getting Started

```bash
git clone https://github.com/Aureliolo/ai-company.git
cd ai-company
uv sync
```

See [docs/getting_started.md](docs/getting_started.md) for prerequisites, IDE setup, and the full walkthrough.

## Documentation

- [Getting Started](docs/getting_started.md) - Setup and installation guide
- [Contributing](CONTRIBUTING.md) - Branch, commit, and PR workflow
- [CLAUDE.md](CLAUDE.md) - Code conventions and AI assistant reference
- [Design Specification](DESIGN_SPEC.md) - Full high-level design

## License

[Business Source License 1.1](LICENSE) — converts to Apache 2.0 on 2030-02-27.
