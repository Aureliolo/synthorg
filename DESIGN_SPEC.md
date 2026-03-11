# SynthOrg - High-Level Design Specification

> A framework for building synthetic organizations — autonomous AI agents orchestrated as a virtual company, with configurable roles, hierarchies, communication patterns, and tool access.

---

The design specification has been split into focused documentation pages for better navigation and maintainability. Each page covers a cohesive domain of the framework's design.

## Design Pages

| Page | Sections | Description |
|------|----------|-------------|
| [Design Overview](docs/design/index.md) | Vision, Core Concepts | What SynthOrg is, design principles, glossary |
| [Agents & HR](docs/design/agents.md) | Agent System, HR | Agent identity, roles, hiring, performance, promotions |
| [Organization & Templates](docs/design/organization.md) | Company Structure, Templates | Company types, hierarchy, departments, template system |
| [Communication](docs/design/communication.md) | Communication Architecture | Message bus, delegation, conflict resolution, meetings |
| [Task & Workflow Engine](docs/design/engine.md) | Task Engine | Task lifecycle, execution loops, routing, recovery, shutdown |
| [Memory & Persistence](docs/design/memory.md) | Memory & Persistence | Memory types, backends, retrieval, operational data |
| [Operations](docs/design/operations.md) | Providers, Budget, Tools, Security, Human Interaction | Provider layer, cost management, sandboxing, security, API |

## Supporting Pages

| Page | Description |
|------|-------------|
| [Tech Stack](docs/architecture/tech-stack.md) | Technology choices and engineering conventions |
| [Decision Log](docs/architecture/decisions.md) | All design decisions, organized by domain |
| [Research & Prior Art](docs/reference/research.md) | Framework comparison and scaling research |
| [Industry Standards](docs/reference/standards.md) | MCP, A2A, and other standards |
| [Open Questions & Risks](docs/roadmap/open-questions.md) | Unresolved questions and risk mitigations |
| [Future Vision](docs/roadmap/future-vision.md) | Backlog features and scaling path |
