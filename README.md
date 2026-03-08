# AI Company

[![CI](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml/badge.svg)](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml)

A framework for orchestrating autonomous AI agents as employees within a virtual company structure.

## Concept

AI Company lets you spin up a virtual organization staffed entirely by AI agents. Each agent has a role (CEO, developer, designer, QA, etc.), a personality, and access to real tools, with persistent memory planned for the next milestone. Agents collaborate through structured communication, follow workflows, and produce real artifacts - code, documents, designs, and more.

## Current Capability Snapshot

### Implemented (M0-M4 foundation)

- **Company Config + Core Models** - Strong Pydantic validation, immutable config models, runtime state models
- **Provider Layer** - LiteLLM-based provider abstraction with routing, retry, and rate limiting
- **Budget Tracking** - Cost records, summaries, and coordination analytics models
- **Tool System** - File system tools, git tools, sandbox abstraction, permission gating
- **Single-Agent Engine (M3)** - ReAct/Plan-Execute loops, fail-and-reassign recovery, graceful shutdown
- **Multi-Agent Core (M4)** - Message bus, delegation with loop prevention, conflict resolution, meeting protocols
- **Task Intelligence (M4)** - Task decomposition, routing, assignment strategies, workspace isolation via git worktrees
- **Templates** - Built-in templates, inheritance/merge, rendering, personality presets

### Not implemented yet (planned milestones)

- **Memory Layer (M5)** - Mem0 selected as initial backend behind pluggable protocol ([ADR-001](docs/decisions/ADR-001-memory-layer.md)); implementation pending
- **Budget Enforcement (M5)** - Auto-downgrade, CFO agent logic, cost tiers
- **API Layer (M6)** - REST + WebSocket API, CLI commands
- **Security/Approval System (M7)** - SecOps agent, progressive trust, approval workflows
- **Advanced Product Surface** - Web dashboard, HR workflows, MCP integrations

## Status

**M0-M4 complete. M5 (Memory, Persistence & Budget Enforcement) is next.** See [DESIGN_SPEC.md](DESIGN_SPEC.md) for the full high-level specification.

## Tech Stack

- **Python 3.14+** with FastAPI, Pydantic, Typer
- **uv** as package manager, **Hatchling** as build backend
- **LiteLLM** for multi-provider LLM abstraction
- **structlog** for structured logging and observability
- **Mem0** for agent memory (initial backend, config-driven, swappable via protocol)
- **MCP** for tool integration (planned)
- **Vue 3** for web dashboard (planned)
- **SQLite** → PostgreSQL for data persistence (planned)

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
