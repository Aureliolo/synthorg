# AI Company

[![CI](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml/badge.svg)](https://github.com/Aureliolo/ai-company/actions/workflows/ci.yml)

A framework for orchestrating autonomous AI agents as employees within a virtual company structure.

## Concept

AI Company lets you spin up a virtual organization staffed entirely by AI agents. Each agent has a role (CEO, developer, designer, QA, etc.), a personality, persistent memory, and access to real tools. Agents collaborate through structured communication, follow workflows, and produce real artifacts - code, documents, designs, and more.

## Key Features (Planned)

- **Any Company Structure** - From a 2-person startup to a 50+ enterprise, defined via config/templates
- **Deep Agent Identity** - Names, personalities, skills, seniority levels, performance tracking
- **Multi-Provider** - Any LLM via LiteLLM — cloud APIs, OpenRouter (400+ models), local Ollama, and more
- **Smart Cost Management** - Per-agent budget tracking, auto model routing, CFO agent optimization
- **Hierarchical Delegation** - Chain-of-command task delegation with five-mechanism loop prevention
- **Conflict Resolution** - Pluggable strategies for resolving agent disagreements (authority, debate, human escalation, hybrid) with dissent audit trail
- **Task Decomposition & Routing** - DAG-based subtask decomposition, structure classification, and agent-task scoring
- **Configurable Autonomy** - From fully autonomous to human-approves-everything, with a Security Ops agent in between
- **Persistent Memory** - Agents remember past decisions, code, relationships (memory layer TBD)
- **HR System** - Hire, fire, promote agents. HR agent analyzes skill gaps and proposes candidates
- **Real Tool Access** - File system, git, code execution, web, databases - role-based and sandboxed
- **API-First** - REST + WebSocket API with local web dashboard
- **Templates + Builder** - Pre-built company templates and interactive builder

## Status

**M3: Single Agent** and **M4: Multi-Agent** in progress (M0 Tooling, M1 Config & Core, M2 Providers — all done). See [DESIGN_SPEC.md](DESIGN_SPEC.md) for the full high-level specification.

## Tech Stack

- **Python 3.14+** with FastAPI, Pydantic, Typer
- **uv** as package manager, **Hatchling** as build backend
- **LiteLLM** for multi-provider LLM abstraction
- **structlog** for structured logging and observability
- **Memory layer TBD** (candidates: Mem0, Zep, Letta, Cognee, custom) for agent memory (planned)
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
