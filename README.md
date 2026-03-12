<p align="center">
  <strong>SynthOrg</strong>
</p>

<p align="center">
  A framework for building synthetic organizations — autonomous AI agents orchestrated as a virtual company.
</p>

<p align="center">
  <a href="https://github.com/Aureliolo/synthorg/actions/workflows/ci.yml"><img src="https://github.com/Aureliolo/synthorg/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/Aureliolo/synthorg"><img src="https://codecov.io/gh/Aureliolo/synthorg/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://github.com/Aureliolo/synthorg/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-BSL_1.1-blue" alt="License"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.14%2B-blue" alt="Python"></a>
  <a href="https://synthorg.io/docs"><img src="https://img.shields.io/badge/docs-synthorg.io-purple" alt="Docs"></a>
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

Define agents with roles, models, and tools. The engine handles task decomposition, routing, execution loops (ReAct, Plan-and-Execute), and multi-agent coordination.

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

### Development

```bash
git clone https://github.com/Aureliolo/synthorg.git
cd synthorg
uv sync                  # install dev + test deps
uv sync --group docs     # install docs toolchain (zensical)
```

### Docker Compose

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
```

## Documentation

| Section | Description |
|---------|-------------|
| [Design Specification](docs/design/index.md) | Vision, agents, communication, engine, memory, operations |
| [Architecture](docs/architecture/index.md) | System overview, tech stack, decision log |
| [API Reference](docs/rest-api.md) | REST API reference (Scalar/OpenAPI) |
| [Library Reference](docs/api/index.md) | Auto-generated from docstrings |
| [Developer Setup](docs/getting_started.md) | Clone, test, lint, contribute |
| [User Guide](docs/user_guide.md) | Install, configure, run via Docker |

> **Contributors:** Start with the [Design Overview](docs/design/index.md) before implementing any feature — it is the mandatory starting point for architecture, data models, and behavior. [`DESIGN_SPEC.md`](DESIGN_SPEC.md) serves as a pointer to the full design set.

## Status

Core framework complete — agent engine, multi-agent coordination, API, security, HR, memory (including Mem0 backend adapter), and budget systems are implemented. Web dashboard (Vue 3 + PrimeVue + Tailwind CSS) is built. Remaining: approval workflow gates, CLI. See the [roadmap](docs/roadmap/index.md) for details.

## License

[Business Source License 1.1](LICENSE) — converts to Apache 2.0 on 2030-02-27.
