# SynthOrg Documentation

**Framework for building synthetic organizations** — autonomous AI agents orchestrated as a virtual company.

SynthOrg lets you define agents with roles, hierarchy, budgets, and tools, then orchestrate them to collaborate on complex tasks as a virtual organization.

---

## Get Started

<div class="grid cards" markdown>

-   :material-play-circle:{ .lg .middle } **Use SynthOrg**

    ---

    Pick a template and run your first synthetic org via Docker.

    [:octicons-arrow-right-24: User Guide](user_guide.md)

-   :material-code-braces:{ .lg .middle } **Develop SynthOrg**

    ---

    Clone the repo, set up your dev environment, and contribute.

    [:octicons-arrow-right-24: Developer Setup](getting_started.md)

</div>

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

| Section | Description |
|---------|-------------|
| [User Guide](user_guide.md) | Install, configure, and run SynthOrg |
| [Developer Setup](getting_started.md) | Clone, test, lint, and contribute |
| [Architecture](architecture/index.md) | System overview, design principles |
| [API Reference](api/index.md) | Auto-generated from docstrings |

---

## Links

- [GitHub Repository](https://github.com/Aureliolo/synthorg)
- [Design Specification](design_spec.md)
- [License](https://github.com/Aureliolo/synthorg/blob/main/LICENSE) (BSL 1.1 → Apache 2.0 on 2030-02-27)
