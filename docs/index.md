# SynthOrg Documentation

**Framework for building synthetic organizations** — autonomous AI agents orchestrated as a virtual company.

SynthOrg lets you define agents with roles, hierarchy, budgets, and tools, then orchestrate them to collaborate on complex tasks as a virtual organization.

!!! warning "Under Active Development"

    SynthOrg is under active development. Many features described in the design specification
    are planned but not yet implemented. See the [Roadmap](roadmap/index.md) for current status.

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

## Design Specification

The design spec covers the full architecture of SynthOrg — from agent identity to budget enforcement:

<div class="grid cards" markdown>

-   **Design Overview**

    ---

    Vision, principles, core concepts, and glossary.

    [:octicons-arrow-right-24: Design Overview](design/index.md)

-   **Agents & HR**

    ---

    Agent identity, roles, hiring, performance tracking, promotions.

    [:octicons-arrow-right-24: Agents](design/agents.md)

-   **Organization & Templates**

    ---

    Company types, hierarchy, departments, template system.

    [:octicons-arrow-right-24: Organization](design/organization.md)

-   **Communication**

    ---

    Message bus, delegation, conflict resolution, meeting protocols.

    [:octicons-arrow-right-24: Communication](design/communication.md)

-   **Task & Workflow Engine**

    ---

    Task lifecycle, execution loops, routing, recovery, shutdown.

    [:octicons-arrow-right-24: Engine](design/engine.md)

-   **Memory & Persistence**

    ---

    Memory types, backends, retrieval pipeline, operational data.

    [:octicons-arrow-right-24: Memory](design/memory.md)

-   **Operations**

    ---

    LLM providers, budget, tools, security, human interaction.

    [:octicons-arrow-right-24: Operations](design/operations.md)

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

## Further Reading

| Section | Description |
|---------|-------------|
| [Architecture](architecture/index.md) | System overview, module map, design principles |
| [Tech Stack](architecture/tech-stack.md) | Technology choices and engineering conventions |
| [Decision Log](architecture/decisions.md) | All design decisions, organized by domain |
| [API Reference](rest-api.md) | REST API reference (Scalar/OpenAPI) |
| [Library Reference](api/index.md) | Auto-generated from docstrings |
| [Roadmap](roadmap/index.md) | Status, open questions, future vision |

---

## Links

- [GitHub Repository](https://github.com/Aureliolo/synthorg)
- [License](https://github.com/Aureliolo/synthorg/blob/main/LICENSE) (BSL 1.1, source available — free production use for non-competing small orgs; converts to Apache 2.0 three years after release)
- [Licensing & Usage](licensing.md) — what's permitted, why BSL, and how to get a commercial license
