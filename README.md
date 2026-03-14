<p align="center">
  <strong>SynthOrg</strong>
</p>

<p align="center">
  A framework for building synthetic organizations — autonomous AI agents orchestrated as a virtual company.
</p>

<p align="center">
  <a href="https://github.com/Aureliolo/synthorg/actions/workflows/ci.yml"><img src="https://github.com/Aureliolo/synthorg/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/Aureliolo/synthorg"><img src="https://codecov.io/gh/Aureliolo/synthorg/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://github.com/Aureliolo/synthorg/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-BSL_1.1_(source_available)-blue" alt="License"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.14%2B-blue" alt="Python"></a>
  <a href="https://synthorg.io/docs"><img src="https://img.shields.io/badge/docs-synthorg.io-purple" alt="Docs"></a>
  <a href="https://securityscorecards.dev/viewer/?uri=github.com/Aureliolo/synthorg"><img src="https://api.securityscorecards.dev/projects/github.com/Aureliolo/synthorg/badge" alt="OpenSSF Scorecard"></a>
  <a href="https://slsa.dev"><img src="https://slsa.dev/images/gh-badge-level3.svg" alt="SLSA 3"></a>
</p>

---

## What is SynthOrg?

SynthOrg lets you define agents with roles, personalities, budgets, and tools, then orchestrate them to collaborate on complex tasks as a virtual organization. Each agent has a defined role (CEO, developer, designer, QA), persistent memory, and access to real tools. Agents collaborate through structured communication, follow workflows, and produce real artifacts — code, documents, designs, and more.

The framework is provider-agnostic (any LLM via LiteLLM), configuration-driven (YAML + Pydantic), and designed for the full autonomy spectrum — from locked-down human approval of every action to fully autonomous operation.

## Capabilities

<table>
<tr>
<td width="33%">

**Agent Orchestration**

Define agents with roles, models, and tools. The engine handles task decomposition, routing, execution loops (ReAct, Plan-and-Execute), crash recovery (checkpoint resume), and multi-agent coordination.

</td>
<td width="33%">

**Budget & Cost Management**

Per-agent cost limits, auto-downgrade to cheaper models at task boundaries, spending reports, CFO-level cost optimization with anomaly detection.

</td>
<td width="33%">

**Security & Trust**

SecOps agent with fail-closed rule engine, progressive trust (4 strategies), configurable autonomy levels, audit logging, and approval timeout policies.

</td>
</tr>
<tr>
<td>

**Memory**

Per-agent and shared organizational memory with retrieval pipeline, non-inferable filtering, consolidation, and archival. Pluggable backends via protocol.

</td>
<td>

**Communication**

Message bus, hierarchical delegation with loop prevention, conflict resolution (4 strategies), and meeting protocols (round-robin, position papers, structured phases).

</td>
<td>

**Tools & Integration**

Built-in tools (file system, git, sandbox, code runner) plus MCP bridge for external tools. Layered sandboxing with subprocess and Docker backends.

</td>
</tr>
</table>

## Quick Start

### Install CLI

```bash
# Linux / macOS
curl -sSfL https://synthorg.io/get/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://synthorg.io/get/install.ps1 | iex
```

### Setup & Run

```bash
synthorg init       # interactive setup wizard
synthorg start      # pull images + start containers
synthorg status     # check health
synthorg doctor     # diagnostics if something is wrong
```

### Development (from source)

```bash
git clone https://github.com/Aureliolo/synthorg.git
cd synthorg
uv sync                  # install dev + test deps
uv sync --group docs     # install docs toolchain (zensical)
```

### Docker Compose (manual)

```bash
cp docker/.env.example docker/.env
docker compose -f docker/compose.yml up -d
curl http://localhost:8000/api/v1/health   # verify
docker compose -f docker/compose.yml down  # stop
```

## Architecture

```mermaid
graph TB
    Config[Config & Templates] --> Engine[Agent Engine]
    Engine --> Core[Core Models]
    Engine --> Providers[LLM Providers]
    Engine --> Communication[Communication]
    Engine --> Tools[Tools & MCP]
    Engine --> Memory[Memory]
    Engine --> Security[Security & Trust]
    Engine --> Budget[Budget & Cost]
    Engine --> HR[HR Engine]
    API[REST & WebSocket API] --> Engine
    Observability[Observability] -.-> Engine
    Persistence[Persistence] -.-> HR
    Persistence -.-> Security
    Persistence -.-> Engine
```

## Documentation

| Section | Description |
|---------|-------------|
| [Design Specification](docs/design/index.md) | Vision, agents, communication, engine, memory, operations |
| [Architecture](docs/architecture/index.md) | System overview, tech stack, decision log |
| [API Reference](docs/rest-api.md) | REST API reference (Scalar/OpenAPI) |
| [Library Reference](docs/api/index.md) | Auto-generated from docstrings |
| [Security](docs/security.md) | Security architecture, hardening, CI/CD security |
| [Developer Setup](docs/getting_started.md) | Clone, test, lint, contribute |
| [User Guide](docs/user_guide.md) | Install, configure, run via Docker |

> **Contributors:** Start with the [Design Overview](docs/design/index.md) before implementing any feature — it is the mandatory starting point for architecture, data models, and behavior. [`DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) serves as a pointer to the full design set.

## Status

Core framework complete — agent engine, multi-agent coordination, API, security, HR, memory (including Mem0 backend adapter), budget systems, and approval workflow gates are implemented. Web dashboard (Vue 3 + PrimeVue + Tailwind CSS) is built. CLI (Go binary) provides install, setup wizard, container lifecycle, self-updates, and diagnostics. Remaining: approval resume scheduler (approvals can park agents; re-enqueue after decision requires a future scheduler). See the [roadmap](docs/roadmap/index.md) for details.

## License

[Business Source License 1.1](LICENSE) — free production use for non-competing organizations with fewer than 500 employees and contractors. Each version converts to Apache 2.0 three years after release. See [licensing details](https://synthorg.io/docs/licensing/) for the full rationale and what's permitted.
