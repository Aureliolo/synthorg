# AI Company

[![CI](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml/badge.svg)](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml)

A framework for orchestrating autonomous AI agents as employees within a virtual company structure.

## Concept

AI Company lets you spin up a virtual organization staffed entirely by AI agents. Each agent has a role (CEO, developer, designer, QA, etc.), a personality, persistent memory, and access to real tools. Agents collaborate through structured communication, follow workflows, and produce real artifacts - code, documents, designs, and more.

## Current Capability Snapshot

### Implemented (M0–M4 complete, M5 in progress)

- **Company Config + Core Models** - Strong Pydantic validation, immutable config models, runtime state models
- **Provider Layer** - LiteLLM-based provider abstraction with routing, retry, and rate limiting
- **Budget Tracking** - Cost records, summaries, and coordination analytics models
- **Tool System** - File system tools, git tools, sandbox abstraction, permission gating
- **Single-Agent Engine (M3)** - ReAct/Plan-Execute loops, fail-and-reassign recovery, graceful shutdown
- **Multi-Agent Core (M4)** - Message bus, delegation with loop prevention, conflict resolution, meeting protocols
- **Task Intelligence (M4)** - Task decomposition, routing, assignment strategies, workspace isolation via git worktrees
- **Templates** - Built-in templates, inheritance/merge, rendering, personality presets
- **Persistence Layer (M5)** - Pluggable `PersistenceBackend` protocol with SQLite backend (aiosqlite), repository protocols, schema migrations
- **Memory Interface (M5)** - Pluggable `MemoryBackend` protocol with capability discovery, shared knowledge protocol, domain models, config, and factory
- **Coordination Error Taxonomy (M5)** - Post-execution classification pipeline detecting logical contradictions, numerical drift, context omissions, and coordination failures
- **Budget Enforcement (M5)** - `BudgetEnforcer` service with pre-flight checks, in-flight budget checking, and auto-downgrade; CFO agent and advanced reporting pending

### Not implemented yet (planned milestones)

- **Memory Backends (M5)** - Mem0 adapter ([ADR-001](docs/decisions/ADR-001-memory-layer.md), #41) pending; shared knowledge store backends planned
- **API Layer (M6)** - `api/` package and route modules are placeholders
- **CLI Surface (M6)** - `cli/` package is placeholder-only
- **Security/Approval System (M7)** - `security/` package is placeholder-only
- **Advanced Product Surface** - web dashboard, HR workflows, progressive trust, and external integrations

## Status

**M5: Memory & Budget** in progress (M0 Tooling, M1 Config & Core, M2 Providers, M3 Single Agent, M4 Multi-Agent — all done). See [DESIGN_SPEC.md](DESIGN_SPEC.md) for the full high-level specification.

## Tech Stack

- **Python 3.14+** with FastAPI, Pydantic, Typer
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
