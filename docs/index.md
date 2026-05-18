# SynthOrg Documentation

**An autonomous product studio you operate**: a self-hostable platform where a synthetic organisation of role-based AI agents is designed to plan and deliver products under budgets and governance.

SynthOrg lets you define agents with roles, hierarchy, budgets, and tools as a virtual organisation. The platform, infrastructure, and subsystem libraries are built and tested; the agent runtime that makes the organisation execute work is in active development.

!!! warning "Honest status: the agent runtime is in active development"

    The platform, dashboard, CLI, persistence, and subsystem libraries are built and tested. The autonomous agent runtime that runs the organisation end to end is **not yet wired** and is the focus of current work. Design-specification pages describe intended behaviour and mark per-area status; treat any gap between a spec and the code as the work, not the spec. See the [Roadmap](roadmap/index.md) for exactly what is available now versus in active development.

---

## Get Started

<div class="grid cards" markdown>

-   :material-play-circle:{ .lg .middle } **Use SynthOrg**

    ---

    Pick a template and stand up the SynthOrg platform via Docker.

    [:octicons-arrow-right-24: User Guide](user_guide.md)

-   :material-code-braces:{ .lg .middle } **Develop SynthOrg**

    ---

    Clone the repo, set up your dev environment, and contribute.

    [:octicons-arrow-right-24: Developer Setup](getting_started.md)

-   :material-book-open-page-variant:{ .lg .middle } **Guides**

    ---

    In-depth guides for configuration, agents, budgets, security, deployment, and more.

    [:octicons-arrow-right-24: Guides](guides/index.md)

</div>

---

## Design Specification

The design spec covers the full intended architecture of SynthOrg, from agent identity to budget enforcement. It is the source of truth for designed behaviour; each area marks its current wiring status, since the agent runtime is in active development.

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

-   **Providers & Cost**

    ---

    LLM providers, budget, tools, security.

    [:octicons-arrow-right-24: Providers](design/providers.md)

</div>

---

## Key capabilities (designed)

These describe the designed system. The provider layer is shipped; the
others are built and unit-tested as components and run via the agent runtime,
which is in active development (see the [Roadmap](roadmap/index.md)).

- **Agent Orchestration**: agents with roles, models, and tools; designed task decomposition, routing, and collaboration.
- **Budget Enforcement**: designed per-agent cost limits, auto-downgrade to cheaper models, spending reports, and CFO-level cost optimisation.
- **Security & Trust**: designed SecOps agent, fail-closed rule engine, progressive trust, autonomy levels, and audit logging.
- **Memory**: designed per-agent and shared organisational memory with retrieval pipeline, consolidation, and archival.
- **Communication**: designed message bus, delegation, conflict resolution, and meeting protocols.
- **HR Engine**: designed hiring, firing, onboarding, offboarding, performance tracking, and promotion criteria.
- **Tool Integration**: built-in tools (file system, git, sandbox, code runner) plus an MCP bridge for external tools.
- **LLM Providers** (available now): provider-agnostic via LiteLLM, with routing strategies, retry/rate-limiting, and capability matching.

---

## Further Reading

| Section | Description |
|---------|-------------|
| [Architecture](architecture/index.md) | System overview, module map, design principles |
| [Tech Stack](architecture/tech-stack.md) | Technology choices and engineering conventions |
| [Decision Log](architecture/decisions.md) | All design decisions, organized by domain |
| [REST API](openapi/index.md) | REST + WebSocket API reference (Scalar/OpenAPI) |
| [Library Reference](api/index.md) | Auto-generated from docstrings |
| [Roadmap](roadmap/index.md) | Status, open questions, future vision |

---

## Links

- [GitHub Repository](https://github.com/Aureliolo/synthorg)
- [License](https://github.com/Aureliolo/synthorg/blob/main/LICENSE) (BUSL 1.1, source available; free production use for non-competing small organisations; converts to Apache 2.0 at the Change Date) <!-- lint-allow: doc-numeric-macros -- "Apache 2.0" is a license version, not a stat claim -->
- [Licensing & Usage](licensing.md): what is permitted, why BUSL, and how to get a commercial license
