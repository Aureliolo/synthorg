# SynthOrg Documentation

**Framework for building synthetic organizations** — autonomous AI agents orchestrated as a virtual company.

SynthOrg lets you define agents with roles, hierarchy, budgets, and tools, then orchestrate them to collaborate on complex tasks as a virtual organization.

---

## Quick Start

```bash
git clone https://github.com/Aureliolo/synthorg.git
cd synthorg
uv sync
```

```python
import asyncio

from ai_company.config.loader import load_config
from ai_company.engine.agent_engine import AgentEngine

async def main():
    config = load_config("company.yaml")
    engine = AgentEngine(config)
    result = await engine.run("Build a REST API for user management")

asyncio.run(main())
```

*API shown is illustrative — see [Getting Started](getting_started.md) for full setup.*

---

## Key Features

- **Agent Orchestration** — Define agents with roles, models, and tools. The engine handles task decomposition, routing, and collaboration.
- **Budget Enforcement** — Per-agent cost limits, auto-downgrade to cheaper models, spending reports, and CFO-level cost optimization.
- **Security & Trust** — SecOps agent, fail-closed rule engine, progressive trust (4 strategies), autonomy levels, and audit logging.
- **Memory** — Per-agent and shared organizational memory with retrieval pipeline, consolidation, and archival.
- **Communication** — Message bus, delegation, conflict resolution (4 strategies), and meeting protocols.
- **HR Engine** — Hiring, firing, onboarding, offboarding, performance tracking, and promotion criteria.
- **Tool Integration** — Built-in tools (file system, git, sandbox, code runner) plus MCP bridge for external tools.
- **LLM Providers** — Provider-agnostic via LiteLLM. Routing strategies, retry/rate-limiting, capability matching.

---

## Documentation

**Use SynthOrg** | **Develop SynthOrg**
--- | ---
[User Guide](user_guide.md) — Install, configure, run | [Developer Setup](getting_started.md) — Clone, install, test, contribute
 | [Architecture](architecture/index.md) — System overview, design principles
 | [API Reference](api/index.md) — Auto-generated from docstrings

---

## Links

- [GitHub Repository](https://github.com/Aureliolo/synthorg)
- [Design Specification](https://github.com/Aureliolo/synthorg/blob/main/DESIGN_SPEC.md)
- [License](https://github.com/Aureliolo/synthorg/blob/main/LICENSE) (BSL 1.1 → Apache 2.0 on 2030-02-27)
