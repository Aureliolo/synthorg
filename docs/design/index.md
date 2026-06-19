---
title: Design Overview
description: Core vision, design principles, and foundational concepts of SynthOrg, an autonomous product studio for synthetic organisations.
---

# Design Overview

## Core Vision

SynthOrg is a **configurable AI company framework** where AI agents operate within a virtual
organisation. Each agent has a defined role, personality, skills, memory, and model backend.
The company can be configured from a 2-person startup to a 50+ enterprise, handling software
development, business operations, creative work, or any domain.

## Design Principles

<div class="grid cards" markdown>

-   **Configuration over Code**

    ---

    Company structures, roles, and workflows are defined via config, not hardcoded.

-   **Provider Agnostic**

    ---

    Any LLM backend: cloud APIs, OpenRouter, Ollama, custom endpoints.

-   **Composable**

    ---

    Mix and match roles, teams, and workflows. Build any type of company.

-   **Observable**

    ---

    Every agent action, communication, and decision is logged and visible.

-   **Autonomy Spectrum**

    ---

    From full human oversight to fully autonomous operation.

-   **Cost Aware**

    ---

    Built-in budget tracking, model routing optimisation, and spending controls.

-   **Extensible**

    ---

    Plugin architecture for new roles, tools, providers, and workflows.

-   **Local First**

    ---

    Runs locally with the option to expose on network or host remotely later.

</div>

## What This Is NOT

- Not a chatbot or conversational AI product
- Not locked to software development only (though that is a primary use case)
- Not a wrapper around a single model or provider
- Not a toy/demo: designed for real, production-quality output
- Not a reasoning parallelizer. Single-agent reasoning is typically more token-efficient on isolated multi-hop questions, and SynthOrg's [auto topology selector](coordination.md#task-decomposability--coordination-topology) defaults to single-agent for such tasks. SynthOrg's value is role-specialised work-stream parallelism, organisational simulation fidelity, and audit-grade decision trails, not reasoning parallelism. See [S1 Multi-Agent Architecture Decision](../research/s1-multi-agent-decision.md) for the full reconciliation.

!!! warning "Pre-alpha: heavy development, not yet usable"

    SynthOrg is in active pre-alpha development. The framework is not yet
    production-ready: expect bugs, rough edges, missing polish, and
    breaking changes between releases. Operator-facing onboarding (real
    provider, real workloads, dashboard polish) has not been exercised end
    to end. Use it for research and contribution, not for real workloads.

    These pages describe the **designed** behaviour of SynthOrg and are the
    source of truth for that design. Protocol interfaces and pluggable
    strategies are designed upfront to inform architecture; individual
    subsystems may still have a gap between the spec and the code. Treat
    any such gap as the work, not the spec. For what is shipped now versus
    on the roadmap, see the [Roadmap](../roadmap/index.md).

## Configuration Philosophy

The framework follows **progressive disclosure**: users only configure what they need.

1. **Templates** handle 90% of users: pick a template, override 2-3 values, go
2. **Minimal config** for custom setups: everything has sensible defaults
3. **Full config** for power users: every knob exposed but none required

**Minimal custom company** (all other settings use defaults):

```yaml
company:
  name: "Acme Corp"
  template: "startup"
  budget_monthly: 50.00
```

All configuration systems in the framework are **pluggable**: strategies, backends, and
policies are swappable via protocol interfaces without modifying existing code. Sensible
defaults are chosen for each, documented in the relevant section alongside the full
configuration reference.

---

## Glossary

| Term | Definition |
|------|-----------|
| **Agent** | An AI entity with a role, personality, model backend, memory, and tool access. The primary entity in the framework. Within a company context, agents serve as the company's employees. |
| **Company** | A configured organisation of agents with structure, hierarchy, and workflows |
| **Department** | A grouping of related roles (Engineering, Product, Design, Operations, etc.) |
| **Role** | A job definition with required skills, responsibilities, authority level, and tool access |
| **Skill** | A capability an agent possesses (coding, writing, analysis, design, etc.) |
| **Task** | A unit of work assigned to one or more agents |
| **Project** | A collection of related tasks with a goal, deadline, and assigned team |
| **Meeting** | A structured multi-agent interaction for decisions, reviews, or planning |
| **Artifact** | Any output produced by agents: code, documents, designs, reports, etc. |

## Entity Relationships

The following diagram illustrates how the core entities in SynthOrg relate to each other:

```d2
Company -> Departments
Company -> Projects
Company -> Config
HR: HR Registry
Company -> HR

Departments -> DeptHead {style.stroke-dash: 5}
DeptHead: "Department Head\n(Agent, optional)"
Departments -> Members
Members: "Members (Agent[])"

Projects -> Tasks
Projects -> Team
Team: "Team (Agent[])"

Tasks -> Assigned
Assigned: "Assigned Agent(s)"
Tasks -> Artifacts
Tasks -> Status
Status: "Status / History"

Config -> Autonomy
Autonomy: Autonomy Level
Config -> Budget
Config -> CommSettings
CommSettings: Communication Settings
Config -> ToolPerms
ToolPerms: Tool Permissions

HR -> Active
Active: "Active Agents[]"
HR -> Roles
Roles: "Available Roles[]"
HR -> Queue
Queue: Hiring Queue
```

---

<div class="grid cards" markdown>

-   [**Agents**](agents.md) / [**HR & Agent Lifecycle**](hr-lifecycle.md)

    ---

    Agent identity (personality, skills, identity versioning) plus the full
    HR lifecycle: seniority, role catalog, hiring, firing, performance tracking,
    evaluation, promotions, and evolution.

-   [**Organisation & Templates**](organization.md)

    ---

    Company types, organisational hierarchy, department configuration, template system,
    and dynamic scaling.

-   [**Communication**](communication.md)

    ---

    Message bus, delegation, conflict resolution, and meeting protocols.

-   [**Engine**](engine.md)

    ---

    Task lifecycle, decomposition, routing, workflow types, and the task engine.

-   [**Agent Execution**](agent-execution.md)

    ---

    The agent execution loop: brain/hands/session model, context budget and
    compaction, and termination conditions.

-   [**Memory**](memory.md)

    ---

    Agent memory, retrieval pipeline, shared organisational memory, and consolidation.

-   [**Project Brain**](project-brain.md)

    ---

    Structured, queryable per-project state store: decisions and rationale, open
    questions, blockers, risks, dependencies, and the evolving plan. Append-only,
    versioned in the workspace, and queried by agents on resume and by the operator.

-   [**Mid-Flight Steering**](mid-flight-steering.md)

    ---

    The operator injects a hint or redirect into a running project; in-flight and
    newly-spawned agents adopt it at safe boundaries, redirects force a re-plan,
    and obsolete work is superseded, recorded in the brain with its rationale.

-   [**Semantic Ontology**](ontology.md)

    ---

    Shared entity vocabulary, versioned definitions, drift detection, and context
    injection for inter-agent semantic alignment.

-   [**Providers**](providers.md) / [**Budget**](budget.md) / [**Tools**](tools.md) / [**Security**](security.md)

    ---

    LLM provider abstraction, budget enforcement, tool sandboxing, progressive trust, autonomy levels, and approval workflows.

-   [**Observability**](observability.md) / [**Notifications**](notifications.md) / [**Backup**](backup.md) / [**Deployment**](deployment.md)

    ---

    Structured logging, correlation tracking, operator alerts,
    backup-and-restore, and container runtime.

-   [**Integrations**](integrations.md)

    ---

    External service connection catalog, OAuth 2.1, webhooks, health checks, rate
    limiting, MCP catalog, and tunnel.

-   [**Persistence**](persistence.md)

    ---

    Repository protocol abstraction, SQLite and Postgres backends, append-only
    time-series tables, TimescaleDB hypertables, and extension strategy.

-   [**Brand Identity & UX**](brand-and-ux.md)

    ---

    Visual identity, semantic colour system, theme architecture, typography, density,
    and animation guidelines.

</div>
