---
title: Design Overview
description: What SynthOrg is built to do, the design principles behind it, and the vocabulary the other design pages use.
---

# Design Overview

**Describe a piece of software. It gets built in one pass: split into parts, built in
parallel, each part checked by something that did not write it. On your hardware, against
any models you choose.**

## Why decomposition, not speed

A single agent working alone cannot hold a whole application: it does one thing at a time,
and the twentieth thing damages the first. Adding agents does not fix that by itself,
because the binding constraint is not agent supply, it is **decomposition quality**. So the
objective is decomposed recursively into a tree of independently buildable units, the leaves
are built concurrently in isolated containers, and the tree is assembled bottom-up, with
every piece checked by something that did not write it. The [documentation home](../index.md)
covers the full mechanism; the cards below link straight to it.

!!! warning "Pre-alpha: heavy development, not yet usable"

    SynthOrg is in active pre-alpha development. The loop has been driven live against a
    real deployment twelve times and has never reached the assembly stage: no run has
    produced an assembled deliverable, and no completion has been recorded. Expect bugs,
    rough edges, missing polish, and breaking changes between releases.

    These pages describe the **designed** behaviour of SynthOrg and are the source of truth
    for that design. Protocol interfaces and pluggable strategies are designed upfront to
    inform architecture; individual subsystems may still have a gap between the spec and the
    code. Treat any such gap as the work, not the spec. Nothing here promises working
    software: every claim is about how the system is built and how it behaves, never about
    what a run will deliver. For what is wired versus what is intent, see the
    [Roadmap](../roadmap/index.md).

## The mechanism

<div class="grid cards" markdown>

-   [**Recursive Decomposition**](recursive-decomposition.md)

    ---

    How an objective becomes a tree of independently buildable units, the atomicity gate,
    and the recursion bounds.

-   [**Coordination**](coordination.md)

    ---

    Dependency-gated waves, parallel dispatch, parking, and run recovery.

-   [**Agent Execution**](agent-execution.md)

    ---

    The execution loop: brain/hands/session model, sandboxed tool use, context budget, and
    termination.

-   [**Verification & Quality**](verification-quality.md)

    ---

    Who reviews, why the reviewer cannot be the author, and what a verdict does and does
    not mean.

-   [**Initiative Tail**](initiative-tail.md)

    ---

    Assembly and evaluation: the only path by which a plan reaches completion.

-   [**Providers**](providers.md) / [**Budget**](budget.md) / [**Security**](security.md)

    ---

    Explicit provider binding, cost recording and spending controls, autonomy levels, and
    the approval gate.

</div>

## Design principles

<div class="grid cards" markdown>

-   **Config over code**

    ---

    Roles, workflows, and org structure are defined via configuration, not hardcoded.

-   **Provider agnostic**

    ---

    Every LLM dispatch names its own explicit `(provider, model)` pair, including local
    models. No default provider to fall back on.

-   **Pluggable**

    ---

    Strategies, backends, and policies are swappable behind protocol interfaces without
    modifying existing code.

-   **Observable**

    ---

    Every action, decision, and state transition is logged, correlated, and auditable.

-   **Supervised by design**

    ---

    An operator sets the oversight mode; destructive or high-risk actions are gated for
    human approval. See [Security](security.md).

-   **Cost aware**

    ---

    Budgets, per-agent limits, and spend attribution are built in.

-   **Self-hosted**

    ---

    Runs on your hardware, provider-agnostic including local models. Your code does not
    leave your machine.

</div>

## What this is not

- Not a chatbot or a conversational product on its own; the conversational front door is one
  entry point onto the same build pipeline, not the product itself.
- Not a wrapper around a single model or provider.
- Not a toy or a demo: it targets real, production-quality output, though no run has
  delivered one yet (see the pre-alpha warning above).
- Not a reasoning parallelizer for isolated multi-hop questions. Single-agent reasoning is
  typically more token-efficient there, and SynthOrg's
  [auto topology selector](coordination.md#task-decomposability-coordination-topology)
  defaults to single-agent for such tasks. SynthOrg's value is splitting a whole application
  into genuinely independent, independently reviewed parts. See
  [S2: agent-parallelism evidence](../research/s2-agent-parallelism-evidence.md) for what the
  research does and does not support; it also closed organisational simulation as an
  output-quality mechanism, which earlier design documentation had asserted.

## Configuration Philosophy

The framework follows **progressive disclosure**: users only configure what they need.

1. **Templates** handle most users: pick a template, override a few values, go
2. **Minimal config** for custom setups: everything has sensible defaults
3. **Full config** for power users: every knob exposed but none required

**Minimal custom deployment** (all other settings use defaults):

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
| **Agent** | An AI entity with a role, model backend, memory, and tool access. The primary entity in the framework. Within a company context, agents serve as the company's employees. |
| **Company** | A configured organisation of agents with structure, hierarchy, and workflows |
| **Department** | A grouping of related roles (Engineering, Product, Design, Operations, etc.) |
| **Role** | A job definition with required skills, responsibilities, authority level, and tool access |
| **Skill** | A capability an agent possesses (coding, writing, analysis, design, etc.) |
| **Task** | A unit of work assigned to one or more agents |
| **Project** | An initiative with a goal, deadline, and assigned team, executing one plan whose items become tasks. Its status advances from that work (see [Project lifecycle](project-lifecycle.md)) |
| **Plan** | The reviewed decomposition of an objective into ordered items; approving one dispatches its work items as tasks (see [Plan Review](plan-review.md)) |
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

Projects -> Plan
Plan: "Plan (current)"
Projects -> Tasks
Projects -> Team
Team: "Team (Agent[])"

Plan -> PlanItems
PlanItems: "Items (work / decision)"
PlanItems -> Tasks {style.stroke-dash: 5}

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

    Agent identity (skills, identity versioning) plus the full
    HR lifecycle: role catalog, reporting-graph authority, hiring, firing,
    performance tracking, evaluation, and evolution.

-   [**Organisation & Templates**](organization.md)

    ---

    Company types, organisational hierarchy, department configuration, template system,
    and headcount changes.

-   [**Communication**](communication.md)

    ---

    Message bus, delegation, loop prevention, and the event stream.

-   [**Engine**](engine.md)

    ---

    Task lifecycle, decomposition, routing, workflow types, and the task engine.

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
    newly-spawned agents adopt it at safe boundaries, and obsolete work is cleanly
    superseded, recorded in the brain with its rationale.

-   [**The Org Asks**](org-questions.md)

    ---

    A standing directive instructs every agent to ask rather than guess when a choice
    is material and hard to reverse; the parked question surfaces in the unified
    conversation and answering it there resumes the run.

-   [**Semantic Ontology**](ontology.md)

    ---

    Shared entity vocabulary, versioned definitions, drift detection, and context
    injection for inter-agent semantic alignment.

-   [**Tools**](tools.md)

    ---

    Tool sandboxing, MCP surface, and the governed connection catalog.

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
