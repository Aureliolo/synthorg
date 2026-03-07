# AI Company - High-Level Design Specification

> A framework for orchestrating autonomous AI agents within a virtual company structure, with configurable roles, hierarchies, communication patterns, and tool access.

---

## Table of Contents

1. [Vision & Philosophy](#1-vision--philosophy) — 1.4 MVP Definition, 1.5 Configuration Philosophy
2. [Core Concepts](#2-core-concepts)
3. [Agent System](#3-agent-system)
4. [Company Structure](#4-company-structure)
5. [Communication Architecture](#5-communication-architecture) — 5.6 Conflict Resolution, 5.7 Meeting Protocol
6. [Task & Workflow Engine](#6-task--workflow-engine) — 6.5 Execution Loop, 6.6 Crash Recovery, **6.7 Graceful Shutdown**, **6.8 Workspace Isolation**, **6.9 Task Decomposability & Coordination Topology**
7. [Memory & Persistence](#7-memory--persistence) — 7.4 Shared Org Memory (Research Directions)
8. [HR & Workforce Management](#8-hr--workforce-management)
9. [Model Provider Layer](#9-model-provider-layer)
10. [Cost & Budget Management](#10-cost--budget-management)
11. [Tool & Capability System](#11-tool--capability-system) — 11.3 Progressive Trust
12. [Security & Approval System](#12-security--approval-system) — 12.4 Approval Timeout
13. [Human Interaction Layer](#13-human-interaction-layer)
14. [Templates & Builder](#14-templates--builder)
15. [Technical Architecture](#15-technical-architecture) — 15.5 Engineering Conventions
16. [Research & Prior Art](#16-research--prior-art) — **16.3 Agent Scaling Research**, 16.4 Build vs Fork Decision
17. [Open Questions & Risks](#17-open-questions--risks)
18. [Backlog & Future Vision](#18-backlog--future-vision)

---

## 1. Vision & Philosophy

### 1.1 Core Vision

Build a **configurable AI company framework** where AI agents operate within a virtual organization. Each agent has a defined role, personality, skills, memory, and model backend. The company can be configured from a 2-person startup to a 50+ enterprise, handling software development, business operations, creative work, or any domain.

### 1.2 Design Principles

| Principle | Description |
|-----------|-------------|
| **Configuration over Code** | Company structures, roles, and workflows defined via config, not hardcoded |
| **Provider Agnostic** | Any LLM backend: cloud APIs, OpenRouter, Ollama, custom endpoints |
| **Composable** | Mix and match roles, teams, workflows. Build any type of company |
| **Observable** | Every agent action, communication, and decision is logged and visible |
| **Autonomy Spectrum** | From full human oversight to fully autonomous operation |
| **Cost Aware** | Built-in budget tracking, model routing optimization, spending controls |
| **Extensible** | Plugin architecture for new roles, tools, providers, and workflows |
| **Local First** | Runs locally with option to expose on network or host remotely later |

### 1.3 What This Is NOT

- Not a chatbot or conversational AI product
- Not locked to software development only (though that is a primary use case)
- Not a wrapper around a single model or provider
- Not a toy/demo - designed for real, production-quality output

### 1.4 MVP Definition (M3)

The MVP validates the core hypothesis: **a single agent can complete a real task end-to-end** within the framework's architecture. It builds on M0–M2 (already complete: config models, provider layer, budget tracking, tool system, observability).

**M3 scope (what the MVP builds):**

- Single agent executing tasks via the **ReAct** execution loop
- **Subprocess sandbox** for file system and git tools (Docker optional for code execution)
- **Fail-and-reassign** crash recovery
- **Cooperative graceful shutdown** with configurable timeout
- **Proxy metrics**: turns/tokens/cost per task
- System prompt builder with agent personality injection

**NOT in MVP (deferred to later milestones):**

- Multi-agent coordination, delegation, message bus (M4)
- Meetings, conflict resolution, meeting protocols (M4)
- Progressive trust (M7) — disabled by default, static access levels only in M3–M6
- HR/CFO agents, hiring/firing, performance tracking (M5–M7)
- Memory layer integration, org memory backends (M5)
- Web UI, WebSocket real-time updates (M6)
- CLI commands beyond basic `start` (M6)
- Security ops agent, approval workflows (M7)

> **How to read this spec:** Sections describe the full vision. Each section with deferred features includes an **MVP** callout box indicating what ships in M3 and what is deferred. The full design is documented upfront to inform architecture decisions — protocol interfaces are designed even for features that won't be built until later milestones.

### 1.5 Configuration Philosophy

The framework follows **progressive disclosure** — users only configure what they need:

1. **Templates** handle 90% of users — pick a template, override 2–3 values, go
2. **Minimal config** for custom setups — everything has sensible defaults
3. **Full config** for power users — every knob exposed but none required

**Minimal custom company** (all other settings use defaults):

```yaml
company:
  name: "Acme Corp"
  template: "startup"
  budget_monthly: 50.00
```

All configuration systems in the framework are **pluggable** — strategies, backends, and policies are swappable via protocol interfaces without modifying existing code. Sensible defaults are chosen for each, documented in the relevant section alongside the full configuration reference.

---

## 2. Core Concepts

### 2.1 Glossary

| Term | Definition |
|------|-----------|
| **Agent** | An AI entity with a role, personality, model backend, memory, and tool access. The primary entity in the framework. Within a company context, agents serve as the company's employees. |
| **Company** | A configured organization of agents with structure, hierarchy, and workflows |
| **Department** | A grouping of related roles (Engineering, Product, Design, Operations, etc.) |
| **Role** | A job definition with required skills, responsibilities, authority level, and tool access |
| **Skill** | A capability an agent possesses (coding, writing, analysis, design, etc.) |
| **Task** | A unit of work assigned to one or more agents |
| **Project** | A collection of related tasks with a goal, deadline, and assigned team |
| **Meeting** | A structured multi-agent interaction for decisions, reviews, or planning |
| **Artifact** | Any output produced by agents: code, documents, designs, reports, etc. |

### 2.2 Entity Relationships

```text
Company
  ├── Departments[]
  │     ├── Department Head (Agent)
  │     └── Members (Agent[])
  ├── Projects[]
  │     ├── Tasks[]
  │     │     ├── Assigned Agent(s)
  │     │     ├── Artifacts[]
  │     │     └── Status / History
  │     └── Team (Agent[])
  ├── Config
  │     ├── Autonomy Level
  │     ├── Budget
  │     ├── Communication Settings
  │     └── Tool Permissions
  └── HR Registry
        ├── Active Agents[]
        ├── Available Roles[]
        └── Hiring Queue
```

---

## 3. Agent System

### 3.1 Agent Identity Card

Every agent has a comprehensive identity. At the design level, agent data splits into two layers:

- **Config (immutable)**: identity, personality, skills, model preferences, tool permissions, authority. Defined at hire time, changed only by explicit reconfiguration. Represented as frozen Pydantic models.
- **Runtime state (mutable-via-copy)**: current status, active task, conversation history, execution metrics. Evolves during agent operation. Represented as Pydantic models using `model_copy(update=...)` for state transitions — never mutated in place.

> **Current state (M3):** Both layers are implemented. Config layer: `AgentIdentity` (frozen, in `core/agent.py`). Runtime state layer: `TaskExecution`, `AgentContext`, `AgentContextSnapshot` (frozen + `model_copy`, in `engine/`). `AgentEngine` orchestrates execution via `run()`. All identifier/name fields use `NotBlankStr` (from `core.types`) for automatic whitespace rejection; optional identifier fields use `NotBlankStr | None`; tuple fields use `tuple[NotBlankStr, ...]` for per-element validation.

```yaml
# --- Current (M2): Config layer — AgentIdentity (frozen) ---
agent:
  id: "uuid"
  name: "Sarah Chen"
  role: "Senior Backend Developer"
  department: "Engineering"
  level: "Senior"            # Junior, Mid, Senior, Lead, Principal, Director, VP, C-Suite
  personality:
    traits:
      - analytical
      - detail-oriented
      - pragmatic
    communication_style: "concise and technical"
    risk_tolerance: "low"      # low, medium, high
    creativity: "medium"       # low, medium, high
    description: >
      Sarah is a methodical backend developer who prioritizes clean architecture
      and thorough testing. She pushes back on shortcuts and advocates for
      proper error handling. Prefers Pythonic solutions.
  skills:
    primary:
      - python
      - fastapi
      - postgresql
      - system-design
    secondary:
      - docker
      - redis
      - testing
  model:
    provider: "example-provider"     # example provider
    model_id: "example-medium-001"   # example model — actual models TBD per agent/role
    temperature: 0.3
    max_tokens: 8192
    fallback_model: "openrouter/example-medium-001"  # example fallback
  memory:
    type: "persistent"           # persistent, project, session, none
    retention_days: null         # null = forever
  tools:
    allowed:
      - file_system
      - git
      - code_execution
      - web_search
      - terminal
    denied:
      - deployment
      - database_admin
  authority:
    can_approve: ["junior_dev_tasks", "code_reviews"]
    reports_to: "engineering_lead"
    can_delegate_to: ["junior_developers"]
    budget_limit: 5.00          # max USD per task
  hiring_date: "2026-02-27"
  status: "active"              # active, on_leave, terminated (on config model today)

# --- Adopted (M3): Runtime state — engine/ (frozen + model_copy) ---
# TaskExecution wraps Task with evolving execution state:
#   status: TaskStatus             # evolves via with_transition()
#   transition_log: tuple[StatusTransition, ...]
#   accumulated_cost: TokenUsage   # running totals
#   turn_count: int                # LLM turns completed
#   started_at / completed_at: AwareDatetime | None
#
# AgentContext wraps AgentIdentity + TaskExecution with:
#   execution_id: str              # uuid4, unique per run
#   conversation: tuple[ChatMessage, ...]
#   accumulated_cost: TokenUsage   # running totals
#   turn_count: int                # LLM turns completed
#   max_turns: int                 # hard limit (default 20)
#   started_at: AwareDatetime
```

### 3.2 Seniority & Authority Levels

| Level | Authority | Typical Model | Cost Tier |
|-------|----------|---------------|-----------|
| Intern/Junior | Execute assigned tasks only | small / local | $ |
| Mid | Execute + suggest improvements | medium / local | $$ |
| Senior | Execute + design + review others | medium / large | $$$ |
| Lead | All above + approve + delegate | large / medium | $$$ |
| Principal/Staff | All above + architectural decisions | large | $$$$ |
| Director | Strategic decisions + budget authority | large | $$$$ |
| VP | Department-wide authority | large | $$$$ |
| C-Suite (CEO/CTO/CFO) | Company-wide authority + final approvals | large | $$$$ |

### 3.3 Role Catalog (Extensible)

#### C-Suite / Executive

- **CEO** - Overall strategy, final decision authority, cross-department coordination
- **CTO** - Technical vision, architecture decisions, technology choices
- **CFO** - Budget management, cost optimization, resource allocation
- **COO** - Operations, process optimization, workflow management
- **CPO** - Product strategy, roadmap, feature prioritization

#### Product & Design

- **Product Manager** - Requirements, user stories, prioritization, stakeholder communication
- **UX Designer** - User research, wireframes, user flows, usability
- **UI Designer** - Visual design, component design, design systems
- **UX Researcher** - User interviews, analytics, A/B test design
- **Technical Writer** - Documentation, API docs, user guides

#### Engineering

- **Software Architect** - System design, technology decisions, patterns
- **Frontend Developer** (Junior/Mid/Senior) - UI implementation, components, state management
- **Backend Developer** (Junior/Mid/Senior) - APIs, business logic, databases
- **Full-Stack Developer** (Junior/Mid/Senior) - End-to-end implementation
- **DevOps/SRE Engineer** - Infrastructure, CI/CD, monitoring, deployment
- **Database Engineer** - Schema design, query optimization, migrations
- **Security Engineer** - Security audits, vulnerability assessment, secure coding

#### Quality Assurance

- **QA Lead** - Test strategy, quality gates, release readiness
- **QA Engineer** - Test plans, manual testing, bug reporting
- **Automation Engineer** - Test frameworks, CI integration, E2E tests
- **Performance Engineer** - Load testing, profiling, optimization

#### Data & Analytics

- **Data Analyst** - Metrics, dashboards, business intelligence
- **Data Engineer** - Pipelines, ETL, data infrastructure
- **ML Engineer** - Model training, inference, MLOps

#### Operations & Support

- **Project Manager** - Timelines, dependencies, risk management, status tracking
- **Scrum Master** - Agile ceremonies, impediment removal, team health
- **HR Manager** - Hiring recommendations, team composition, performance tracking
- **Security Operations** - Request validation, safety checks, approval workflows

#### Creative & Marketing

- **Content Writer** - Blog posts, marketing copy, social media
- **Brand Strategist** - Messaging, positioning, competitive analysis
- **Growth Marketer** - Campaigns, analytics, conversion optimization

### 3.4 Dynamic Roles

Users can define custom roles via config:

```yaml
custom_roles:
  - name: "Blockchain Developer"
    department: "Engineering"
    skills: ["solidity", "web3", "smart-contracts"]
    system_prompt_template: "blockchain_dev.md"
    authority_level: "senior"
    suggested_model: "large"
```

---

## 4. Company Structure

### 4.1 Company Types (Templates)

| Template | Size | Roles | Use Case |
|----------|------|-------|----------|
| **Solo Founder** | 1-2 | CEO + Full-Stack Dev | Quick prototypes, solo projects |
| **Startup** | 3-5 | CEO, CTO, 2 Devs, PM | Small projects, MVPs |
| **Dev Shop** | 5-10 | Lead, Sr Dev, Jr Devs, QA, DevOps | Software development focus |
| **Product Team** | 8-15 | PM, Designer, Devs, QA, Data Analyst | Product-focused development |
| **Agency** | 10-20 | Multiple PMs, Designers, Devs, Content | Client work, multiple projects |
| **Full Company** | 20-50+ | All departments, full hierarchy | Enterprise simulation |
| **Research Lab** | 5-10 | Lead Researcher, Analysts, Engineers | Research and analysis |
| **Custom** | Any | User-defined | Anything |

### 4.2 Organizational Hierarchy

```text
                        ┌─────────┐
                        │   CEO   │
                        └────┬────┘
              ┌──────────────┼──────────────┐
         ┌────┴────┐   ┌────┴────┐   ┌─────┴────┐
         │   CTO   │   │   CPO   │   │   CFO    │
         └────┬────┘   └────┬────┘   └────┬─────┘
              │              │              │
    ┌─────────┼────────┐    │         Budget Mgmt
    │         │        │    │
┌───┴───┐ ┌──┴──┐ ┌───┴──┐ ├── Product Managers
│ Eng   │ │ QA  │ │DevOps│ ├── UX/UI Designers
│ Lead  │ │Lead │ │ Lead │ └── Tech Writers
└───┬───┘ └──┬──┘ └──┬───┘
    │        │       │
 Sr Devs   QA Eng  SRE
 Jr Devs   Auto Eng
```

### 4.3 Department Configuration

```yaml
departments:
  engineering:
    head: "cto"
    budget_percent: 60
    teams:
      - name: "backend"
        lead: "backend_lead"
        members: ["sr_backend_1", "mid_backend_1", "jr_backend_1"]
      - name: "frontend"
        lead: "frontend_lead"
        members: ["sr_frontend_1", "mid_frontend_1"]
  product:
    head: "cpo"
    budget_percent: 20
    teams:
      - name: "core"
        lead: "pm_lead"
        members: ["pm_1", "ux_designer_1", "ui_designer_1"]
  operations:
    head: "coo"
    budget_percent: 10
    teams:
      - name: "devops"
        lead: "devops_lead"
        members: ["sre_1"]
  quality:
    head: "qa_lead"
    budget_percent: 10
    teams:
      - name: "qa"
        lead: "qa_lead"
        members: ["qa_engineer_1", "automation_engineer_1"]
```

### 4.4 Dynamic Scaling

The company can dynamically grow or shrink:

- **Auto-scale**: HR agent detects workload increase, proposes new hires
- **Manual scale**: Human adds/removes agents via config or UI
- **Budget-driven**: CFO agent caps headcount based on budget constraints
- **Skill-gap**: HR analyzes team capabilities, identifies missing skills, proposes hires

---

## 5. Communication Architecture

### 5.1 Communication Patterns

The system supports multiple communication patterns, configurable per company:

#### Pattern 1: Event-Driven Message Bus (Recommended Default)

```text
┌──────────┐     ┌─────────────────┐     ┌──────────┐
│  Agent A  │────▶│   Message Bus    │◀────│  Agent B  │
└──────────┘     │  (Topics/Queues) │     └──────────┘
                 └────────┬────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        #engineering  #product   #all-hands
        #code-review  #design    #incidents
```

- Agents publish to topics, subscribe to relevant channels
- Async by default, enables parallelism
- Decoupled - agents don't need to know about each other
- Natural audit trail of all communications
- **Best for**: Most scenarios, scales well, production-ready pattern

#### Pattern 2: Hierarchical Delegation

```text
CEO ──▶ CTO ──▶ Eng Lead ──▶ Sr Dev ──▶ Jr Dev
                    │
                    └──▶ QA Lead ──▶ QA Eng
```

- Tasks flow down the hierarchy, results flow up
- Each level can decompose/refine tasks before delegating
- Authority enforcement built into the flow
- **Best for**: Structured organizations, clear chains of command

#### Pattern 3: Meeting-Based

```text
┌─────────────────────────────────┐
│        Sprint Planning          │
│  PM + CTO + Devs + QA + Design │
│  Output: Sprint backlog         │
└─────────────────────────────────┘
         │
┌────────┴────────┐
│  Daily Standup  │
│  Devs + QA      │
│  Output: Status │
└─────────────────┘
```

- Structured multi-agent conversations at defined intervals
- Standup, sprint planning, retrospective, design review, code review
- **Best for**: Agile workflows, decision-making, alignment

#### Pattern 4: Hybrid (Recommended for Full Company)

Combines all three:
- **Message bus** for async daily work and notifications
- **Hierarchical delegation** for task assignment and approvals
- **Meetings** for cross-team decisions and planning ceremonies

### 5.2 Communication Standards

The framework should align with emerging industry standards:

- **A2A Protocol** (Agent-to-Agent, Linux Foundation) - For inter-agent task delegation, capability discovery via Agent Cards, and structured task lifecycle management
- **MCP** (Model Context Protocol, Agentic AI Foundation / Linux Foundation) - For agent-to-tool integration, providing standardized tool discovery and invocation

### 5.3 Message Format

```json
{
  "id": "msg-uuid",
  "timestamp": "2026-02-27T10:30:00Z",
  "from": "sarah_chen",
  "to": "engineering",
  "type": "task_update",
  "priority": "normal",
  "channel": "#backend",
  "content": "Completed API endpoint for user authentication. PR ready for review.",
  "attachments": [
    {"type": "artifact", "ref": "pr-42"}
  ],
  "metadata": {
    "task_id": "task-123",
    "project_id": "proj-456",
    "tokens_used": 1200,
    "cost_usd": 0.018
  }
}
```

### 5.4 Communication Config

```yaml
communication:
  default_pattern: "hybrid"
  message_bus:
    backend: "internal"        # internal, redis, rabbitmq, kafka
    channels:
      - "#all-hands"
      - "#engineering"
      - "#product"
      - "#design"
      - "#incidents"
      - "#code-review"
      - "#watercooler"
  meetings:
    enabled: true
    types:
      - name: "daily_standup"
        frequency: "per_sprint_day"
        participants: ["engineering", "qa"]
        duration_tokens: 2000
      - name: "sprint_planning"
        frequency: "bi_weekly"
        participants: ["all"]
        duration_tokens: 5000
      - name: "code_review"
        trigger: "on_pr"
        participants: ["author", "reviewers"]
  hierarchy:
    enforce_chain_of_command: true
    allow_skip_level: false    # can a junior message the CEO directly?
```

### 5.5 Loop Prevention

Agent communication loops (A delegates to B who delegates back to A) are a critical risk. The framework enforces multiple safeguards:

| Mechanism | Description | Default |
|-----------|-------------|---------|
| **Max delegation depth** | Hard limit on chain length (A→B→C→D stops at depth N) | 5 |
| **Message rate limit** | Max messages per agent pair within a time window | 10 per minute |
| **Identical request dedup** | Detects and rejects duplicate task delegations within a window | 60s window |
| **Circuit breaker** | If an agent pair exceeds error/bounce threshold, block further messages until manual reset or cooldown | 3 bounces → 5min cooldown |
| **Task ancestry tracking** | Every delegated task carries its full delegation chain; agents cannot delegate back to any ancestor in the chain | Always on |

```yaml
loop_prevention:
  max_delegation_depth: 5
  rate_limit:
    max_per_pair_per_minute: 10
    burst_allowance: 3
  dedup_window_seconds: 60
  circuit_breaker:
    bounce_threshold: 3
    cooldown_seconds: 300
  ancestry_tracking: true       # always on, not configurable
```

When a loop is detected, the framework:
1. Blocks the looping message
2. Notifies the sending agent with the detected loop chain
3. Escalates to the sender's manager (or human if at top of hierarchy)
4. Logs the loop for analytics and process improvement

### 5.6 Conflict Resolution Protocol

When two or more agents disagree on an approach (architecture, implementation, priority, etc.), the framework provides multiple configurable resolution strategies behind a `ConflictResolver` protocol. New strategies can be added without modifying existing ones. The strategy is configurable per company, per department, or per conflict type.

> **MVP: Not in M3.** Conflict resolution is an M4 feature (M3 is single-agent). Authority + Dissent Log (Strategy 1) is the initial default.

#### Strategy 1: Authority + Dissent Log (Default)

The agent with higher authority level decides. Cross-department conflicts (incomparable authority) escalate to the lowest common manager in the hierarchy. The losing agent's reasoning is preserved as a **dissent record** — a structured log entry containing the conflict context, both positions, and the resolution. Dissent records feed into organizational learning and can be reviewed during retrospectives.

```yaml
conflict_resolution:
  strategy: "authority"            # authority, debate, human, hybrid
```

- Deterministic, zero extra tokens, fast resolution
- Dissent records create institutional memory of alternative approaches

#### Strategy 2: Structured Debate + Judge

Both agents present arguments (1 round each, capped at `max_tokens_per_argument`). A judge — their shared manager, the CEO, or a configurable arbitrator agent — evaluates both positions and decides. The judge's reasoning and both arguments are logged as a dissent record.

```yaml
conflict_resolution:
  strategy: "debate"
  debate:
    max_tokens_per_argument: 500
    judge: "shared_manager"        # shared_manager, ceo, designated_agent
```

- Better decisions — forces agents to articulate reasoning
- Higher token cost, adds latency proportional to argument length

#### Strategy 3: Human Escalation

All genuine conflicts go to the human approval queue with both positions summarized. The agent(s) park the conflicting task and work on other tasks while waiting (see §12.4 Approval Timeout).

```yaml
conflict_resolution:
  strategy: "human"
```

- Safest — human always makes the call
- Bottleneck at scale, depends on human availability

#### Strategy 4: Hybrid (Recommended for Production)

Combines strategies with an intelligent review layer:

1. Both agents present arguments (1 round, capped tokens) — preserving dissent
2. A **conflict review agent** evaluates the result:
   - If the resolution is **clear** (one position is objectively better, or authority applies cleanly) → resolve automatically, log dissent record
   - If the resolution is **ambiguous** (genuine trade-offs, no clear winner) → escalate to human queue with both positions + the review agent's analysis

```yaml
conflict_resolution:
  strategy: "hybrid"
  hybrid:
    max_tokens_per_argument: 500
    review_agent: "conflict_reviewer"  # dedicated agent or role
    escalate_on_ambiguity: true
```

- Best balance: most conflicts resolve fast, humans only see genuinely hard calls
- Most complex to implement; review agent itself needs careful prompt design

### 5.7 Meeting Protocol

Meetings (§5.1 Pattern 3) follow configurable protocols that determine how agents interact during structured multi-agent conversations. Different meeting types naturally suit different protocols. All protocols implement a `MeetingProtocol` protocol, making the system extensible — new protocols can be registered and selected per meeting type. Cost bounds are enforced by `duration_tokens` in meeting config (§5.4).

> **MVP: Not in M3.** Meetings are an M4 feature. Round-Robin (Protocol 1) is the initial default.

#### Protocol 1: Round-Robin Transcript

The meeting leader calls each participant in turn. A shared transcript grows as each agent responds, seeing all prior contributions. The leader summarizes and extracts action items at the end.

```yaml
meeting_protocol: "round_robin"
round_robin:
  max_turns_per_agent: 2
  max_total_turns: 16
  leader_summarizes: true
```

- Simple, natural conversation feel, each agent sees full context
- Token cost grows quadratically; last speaker has more context (ordering bias)
- **Best for**: Daily standups, status updates, small groups (3-5 agents)

#### Protocol 2: Async Position Papers + Synthesizer

Each agent independently writes a short position paper (parallel execution, no shared context). A synthesizer agent reads all positions, identifies agreements and conflicts, and produces decisions + action items.

```yaml
meeting_protocol: "position_papers"
position_papers:
  max_tokens_per_position: 300
  synthesizer: "meeting_leader"    # who synthesizes
```

- Cheapest — parallel calls, no quadratic growth, no ordering bias, no groupthink
- Loses back-and-forth dialogue; agents can't challenge each other's ideas
- **Best for**: Brainstorming, architecture proposals, large groups, cost-sensitive meetings

#### Protocol 3: Structured Phases

Meeting split into phases with targeted participation:

1. **Agenda broadcast** — leader shares agenda and context to all participants
2. **Input gathering** — each agent submits input independently (parallel)
3. **Discussion round** — only triggered if conflicts are detected between inputs; relevant agents debate (1 round, capped tokens)
4. **Decision + action items** — leader synthesizes, creates tasks from action items

```yaml
meeting_protocol: "structured_phases"
structured_phases:
  skip_discussion_if_no_conflicts: true
  max_discussion_tokens: 1000
  auto_create_tasks: true          # action items become tasks
```

- Cost-efficient — parallel input, discussion only when needed
- More complex orchestration; conflict detection between inputs needs design
- **Best for**: Sprint planning, design reviews, architecture decisions

---

## 6. Task & Workflow Engine

### 6.1 Task Lifecycle

```text
                 ┌──────────┐
                 │ CREATED   │
                 └─────┬─────┘
                       │ assignment
                 ┌─────▼─────┐
          ┌──────│ ASSIGNED   │
          │      └─────┬─────┘
          │            │ agent starts
          │      ┌─────▼─────┐
          │      │IN_PROGRESS │◀──── (rework)
          │      └─────┬─────┘        │
          │            │ agent done    │
          │      ┌─────▼─────┐        │
          │      │ IN_REVIEW  │───────┘
          │      └─────┬─────┘
          │            │ approved
          │      ┌─────▼─────┐
          │      │ COMPLETED  │
          │      └────────────┘
          │
          │ blocked / cancelled
    ┌─────▼─────┐
    │ BLOCKED /  │
    │ CANCELLED  │
    └────────────┘
```

> **Runtime wrapper (M3):** During execution, `Task` is wrapped by `TaskExecution` (in `engine/task_execution.py`). `TaskExecution` is a frozen Pydantic model that tracks status transitions via `model_copy(update=...)`, accumulates `TokenUsage` cost, and records a `StatusTransition` audit trail. The original `Task` is preserved unchanged; `to_task_snapshot()` produces a `Task` copy with the current execution status for persistence.

### 6.2 Task Definition

```yaml
task:
  id: "task-123"
  title: "Implement user authentication API"
  description: "Create REST endpoints for login, register, logout with JWT tokens"
  type: "development"           # development, design, research, review, meeting, admin
  priority: "high"              # critical, high, medium, low
  project: "proj-456"
  created_by: "product_manager_1"
  assigned_to: "sarah_chen"
  reviewers: ["engineering_lead", "security_engineer"]
  dependencies: ["task-120", "task-121"]
  artifacts_expected:
    - type: "code"
      path: "src/auth/"
    - type: "tests"
      path: "tests/auth/"
    - type: "documentation"
      path: "docs/api/auth.md"
  acceptance_criteria:
    - "JWT-based auth with refresh tokens"
    - "Rate limiting on login endpoint"
    - "Unit and integration tests with >80% coverage"
    - "API documentation"
  estimated_complexity: "medium"  # simple, medium, complex, epic
  task_structure: "parallel"      # sequential, parallel, mixed (M4 — see §6.9)
  budget_limit: 2.00             # max USD for this task
  deadline: null
  status: "assigned"
```

### 6.3 Workflow Types

#### Sequential Pipeline

```text
Requirements ──▶ Design ──▶ Implementation ──▶ Review ──▶ Testing ──▶ Deploy
```

#### Parallel Execution

```text
        ┌──▶ Frontend Dev ──┐
Task ───┤                    ├──▶ Integration ──▶ QA
        └──▶ Backend Dev  ──┘
```

#### Kanban Board

```text
Backlog │ Ready │ In Progress │ Review │ Done
   ○    │   ○   │     ●       │   ○    │  ●●●
   ○    │   ○   │     ●       │        │  ●●
   ○    │       │             │        │  ●
```

#### Agile Sprints

```text
Sprint Backlog → Sprint Execution → Review → Retrospective → Next Sprint
```

### 6.4 Task Routing & Assignment

Tasks can be assigned through multiple strategies:

| Strategy | Description |
|----------|-------------|
| **Manual** | Human or manager explicitly assigns |
| **Role-based** | Auto-assign to agents with matching role/skills |
| **Load-balanced** | Distribute evenly across available agents |
| **Auction** | Agents "bid" on tasks based on confidence/capability |
| **Hierarchical** | Flow down through management chain |
| **Cost-optimized** | Assign to cheapest capable agent |

### 6.5 Agent Execution Loop

The agent execution loop defines how an agent processes a task from start to finish. The framework provides multiple configurable loop architectures behind an `ExecutionLoop` protocol, making the system extensible. The default can vary by task complexity, and is configurable per agent or role.

> **MVP: ReAct only (Loop 1).** Plan-and-Execute and Hybrid are M4+. Auto-selection is M4+.

#### ExecutionLoop Protocol

All loop implementations satisfy the `ExecutionLoop` runtime-checkable protocol (defined in `engine/loop_protocol.py`):

- **`get_loop_type() -> str`** — returns a unique identifier (e.g. `"react"`)
- **`execute(...) -> ExecutionResult`** — runs the loop to completion, accepting `AgentContext`, `CompletionProvider`, optional `ToolInvoker`, optional `BudgetChecker`, and optional `CompletionConfig`

Supporting models:

- **`TerminationReason`** — enum: `COMPLETED`, `MAX_TURNS`, `BUDGET_EXHAUSTED`, `ERROR`
- **`TurnRecord`** — frozen per-turn stats (tokens, cost, tool calls, finish reason)
- **`ExecutionResult`** — frozen outcome with final context, termination reason, turn records, and optional error message (required when reason is `ERROR`)
- **`BudgetChecker`** — callback type `Callable[[AgentContext], bool]` invoked before each LLM call

#### Loop 1: ReAct (Default for Simple Tasks)

A single interleaved loop: the agent reasons about the current state, selects an action (tool call or response), observes the result, and repeats until done or `max_turns` is reached.

```text
┌──────────────────────────────────────────┐
│              ReAct Loop                  │
│                                          │
│  ┌─────────┐   ┌──────┐   ┌──────────┐ │
│  │  Think   │──▶│  Act  │──▶│ Observe  │ │
│  └─────────┘   └──────┘   └────┬─────┘ │
│       ▲                         │        │
│       └─────────────────────────┘        │
│                                          │
│  Terminate when: task complete, max      │
│  turns, budget exhausted, or error       │
└──────────────────────────────────────────┘
```

```yaml
execution_loop: "react"              # react, plan_execute, hybrid, auto
```

- Simple, proven, flexible. Easy to implement. Works well for short tasks
- Token-heavy on long tasks (re-reads full context every turn). No long-term planning — greedy step-by-step
- **Best for**: Simple tasks, quick fixes, single-file changes, M3 MVP

#### Loop 2: Plan-and-Execute

A two-phase approach: the agent first generates a step-by-step plan, then executes each step sequentially. On failure, the agent can replan. Different models can be used for planning vs execution (e.g., large for planning, small for execution steps).

```text
┌──────────────────────────────────────────┐
│           Plan-and-Execute               │
│                                          │
│  ┌──────────┐    ┌───────────────────┐  │
│  │  Plan     │───▶│  Execute Steps    │  │
│  │  (1 call) │    │  (N calls)        │  │
│  └──────────┘    └────────┬──────────┘  │
│       ▲                    │             │
│       └────── replan ──────┘             │
│         (on step failure)                │
└──────────────────────────────────────────┘
```

```yaml
execution_loop: "plan_execute"
plan_execute:
  planner_model: null              # null = use agent's model; override for cost optimization
  executor_model: null
  max_replans: 3
```

- Token-efficient for long tasks. Auditable plan artifact. Supports model tiering
- Rigid — plan may be wrong, replanning is expensive. Over-plans simple tasks
- **Best for**: Complex multi-step tasks, epic-level work, tasks spanning multiple files

#### Loop 3: Hybrid Plan + ReAct Steps (Recommended for Complex Tasks)

The agent creates a high-level plan (3-7 steps). Each step is executed as a mini-ReAct loop with its own turn limit. After each step, the agent checkpoints — summarizing progress and optionally replanning remaining steps. Checkpoints are natural points for human inspection or task suspension.

```text
┌──────────────────────────────────────────────┐
│         Hybrid: Plan + ReAct Steps           │
│                                              │
│  ┌──────────┐                                │
│  │  Plan     │                               │
│  └────┬─────┘                                │
│       │                                      │
│  ┌────▼────────────────────────────────────┐ │
│  │  Step 1: mini-ReAct (think→act→observe) │ │
│  └────┬────────────────────────────────────┘ │
│       │ checkpoint: summarize progress       │
│  ┌────▼────────────────────────────────────┐ │
│  │  Step 2: mini-ReAct                     │ │
│  └────┬────────────────────────────────────┘ │
│       │ checkpoint: replan if needed         │
│  ┌────▼────────────────────────────────────┐ │
│  │  Step N: mini-ReAct                     │ │
│  └─────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

```yaml
execution_loop: "hybrid"
hybrid:
  max_plan_steps: 7
  max_turns_per_step: 5
  checkpoint_after_each_step: true
  allow_replan: true
```

- Strategic planning + tactical flexibility. Natural checkpoints for suspension/inspection
- Most complex to implement. Plan granularity needs tuning per task type
- **Best for**: Complex tasks, multi-file refactoring, tasks requiring both planning and adaptivity

> **Auto-selection (optional):** When `execution_loop: "auto"`, the framework selects the loop based on `estimated_complexity`: simple → ReAct, medium → Plan-and-Execute, complex/epic → Hybrid. Configurable via `auto_loop_rules` — a mapping of complexity thresholds to loop implementations (e.g., `{simple_max_tokens: 500, medium_max_tokens: 3000}` with corresponding loop assignments).

#### AgentEngine Orchestrator

`AgentEngine` (in `engine/agent_engine.py`) is the top-level entry point for running an agent on a task. It composes the execution loop with prompt construction, context management, tool invocation, and cost tracking into a single `run()` call.

**`async run(identity, task, completion_config?, max_turns?, memory_messages?, timeout_seconds?) -> AgentRunResult`**

Pipeline steps:

1. **Validate inputs** — agent must be `ACTIVE`, task must be `ASSIGNED` or `IN_PROGRESS`. Raises `ExecutionStateError` on violation.
2. **Build system prompt** — calls `build_system_prompt()` with agent identity, task, and available tool definitions.
3. **Create context** — `AgentContext.from_identity()` with the configured `max_turns`.
4. **Seed conversation** — injects system prompt, optional memory messages, and formatted task instruction as initial messages.
5. **Transition task** — `ASSIGNED` → `IN_PROGRESS` (pass-through if already `IN_PROGRESS`).
6. **Prepare tools and budget** — creates `ToolInvoker` from registry and `BudgetChecker` from task budget limit.
7. **Delegate to loop** — calls `ExecutionLoop.execute()` with context, provider, tool invoker, budget checker, and completion config. If `timeout_seconds` is set, wraps the call in `asyncio.wait_for`; on expiry the run returns with `TerminationReason.ERROR` but cost recording and post-execution processing still occur.
8. **Record costs** — records accumulated `TokenUsage` to `CostTracker` (if available). Cost recording failures are logged but do not affect the result.
9. **Apply post-execution transitions** — on `COMPLETED` termination: IN_PROGRESS → IN_REVIEW → COMPLETED (two-hop auto-complete in M3; reviewers deferred to M4+). All other termination reasons leave the task in its current state. Transition failures are logged but do not discard the successful execution result.
10. **Return result** — wraps `ExecutionResult` in `AgentRunResult` with engine-level metadata.

Error handling: `MemoryError` and `RecursionError` propagate unconditionally. All other exceptions are caught and wrapped in an `AgentRunResult` with `TerminationReason.ERROR`.

Constructor accepts: `provider` (required), `execution_loop` (defaults to `ReactLoop`), `tool_registry`, `cost_tracker`. The `run()` method also accepts `memory_messages` — optional working memory to inject between the system prompt and task instruction (memory retrieval is M5; the engine provides the injection hook).

Logs structured events under the `execution.engine.*` namespace (12 constants in `events/execution.py`): creation, start, prompt built, completion, errors, invalid input, task transitions, cost recording outcomes, task metrics, and timeout.

**`AgentRunResult`** — frozen Pydantic model wrapping `ExecutionResult` with engine metadata:

- `execution_result` — outcome from the execution loop
- `system_prompt` — the `SystemPrompt` used for this run
- `duration_seconds` — wall-clock run time
- `agent_id`, `task_id` — identifiers
- Computed fields: `termination_reason`, `total_turns`, `total_cost_usd`, `is_success`, `completion_summary`

### 6.6 Agent Crash Recovery

When an agent execution fails unexpectedly (unhandled exception, OOM, process kill), the framework needs a recovery mechanism. Recovery strategies are implemented behind a `RecoveryStrategy` protocol, making the system pluggable — new strategies can be added without modifying existing ones.

> **MVP: Fail-and-Reassign only (Strategy 1).** Checkpoint Recovery is M4/M5.

#### Strategy 1: Fail-and-Reassign (Default / MVP)

The engine catches the failure at its outermost boundary, logs a redacted `AgentContext` snapshot (turn count, accumulated cost — excluding message contents to avoid leaking sensitive prompts/tool outputs), transitions the task to `FAILED`, and makes it available for reassignment (manual or automatic via the task router).

> **New non-terminal state:** `FAILED` is a new `TaskStatus` variant to be added alongside `CANCELLED`. The §6.1 lifecycle diagram and `TaskStatus` enum will be updated when crash recovery is implemented in M3. `FAILED` differs from `CANCELLED` (which is terminal) in that failed tasks are eligible for automatic reassignment.

```yaml
crash_recovery:
  strategy: "fail_reassign"            # fail_reassign, checkpoint
```

- Simple, no persistence dependency, M3-ready
- All progress is lost on crash — acceptable for short single-agent tasks in the MVP

On crash:
1. Catch exception at the engine boundary (outermost `try/except` in the execution loop)
2. Log at ERROR with redacted `AgentContext` snapshot (turn count, accumulated cost, tool call history — message contents excluded)
3. Transition `TaskExecution` → `FAILED` with the exception as the failure reason
4. Task becomes available for reassignment via the task router

#### Strategy 2: Checkpoint Recovery (Planned — M4/M5)

The engine persists an `AgentContext` snapshot after each completed turn. On crash, the framework detects the failure (via heartbeat timeout or exception), loads the last checkpoint, and resumes execution from the exact turn where it left off. The immutable `model_copy(update=...)` pattern makes checkpointing trivial — each `AgentContext` is a complete, self-contained frozen state that serializes cleanly via `model_dump_json()`.

```yaml
crash_recovery:
  strategy: "checkpoint"
  checkpoint:
    persist_every_n_turns: 1           # checkpoint frequency
    storage: "sqlite"                  # sqlite, filesystem
    heartbeat_interval_seconds: 30     # detect unresponsive agents
    max_resume_attempts: 2             # retry limit before falling back to fail_reassign
```

- Preserves progress — critical for long tasks (multi-step plans, epic-level work)
- Requires persistence layer and environment state reconciliation on resume
- Natural fit with the existing immutable state model

> **Environment reconciliation:** When resuming from a checkpoint, the agent's tools and workspace may have changed (other agents modified files, external state drifted). The checkpoint strategy includes a reconciliation step: the resumed agent receives a summary of changes since the checkpoint timestamp and can adapt its plan accordingly. This is analogous to a developer returning to a branch after colleagues have pushed changes.

### 6.7 Graceful Shutdown Protocol

When the process receives SIGTERM/SIGINT (user Ctrl+C, Docker stop, systemd shutdown), the framework needs to stop cleanly without losing work or leaking costs. Shutdown strategies are implemented behind a `ShutdownStrategy` protocol, making the system pluggable — new strategies can be added without modifying existing ones.

> **MVP: Cooperative with Timeout only (Strategy 1).** Other strategies are future options enabled by the protocol interface.

#### Strategy 1: Cooperative with Timeout (Default / MVP)

The engine sets a shutdown event, stops accepting new tasks, and gives in-flight agents a grace period to finish their current turn. Agents check the shutdown event at turn boundaries (between LLM calls, before tool invocations) and exit cooperatively. After the grace period, remaining agents are force-cancelled and their tasks marked `INTERRUPTED`.

```yaml
graceful_shutdown:
  strategy: "cooperative_timeout"    # cooperative_timeout, immediate, finish_tool, checkpoint
  cooperative_timeout:
    grace_seconds: 30                # time for agents to finish cooperatively
    cleanup_seconds: 5               # time for final cleanup (persist cost records, close connections)
```

On shutdown signal:
1. Set `shutdown_event` (`asyncio.Event`) — agents check this at turn boundaries
2. Stop accepting new tasks (drain gate closes)
3. Wait up to `grace_seconds` for agents to exit cooperatively
4. Force-cancel remaining agents (`task.cancel()`) — tasks transition to `INTERRUPTED`
5. Cleanup phase (`cleanup_seconds`): persist cost records, close provider connections, flush logs

> **Planned non-terminal status:** `INTERRUPTED` will be introduced as a new `TaskStatus` variant (and the task status transition map updated) when graceful shutdown is implemented. Unlike `FAILED` (eligible for automatic reassignment) or `CANCELLED` (terminal), `INTERRUPTED` indicates the task was stopped due to process shutdown and is eligible for manual or automatic reassignment on restart.
>
> **Windows compatibility:** `loop.add_signal_handler()` is not supported on Windows. The implementation uses `signal.signal()` as a fallback. SIGINT (Ctrl+C) works cross-platform; SIGTERM on Windows requires `os.kill()`.
>
> **In-flight LLM calls:** Non-streaming API calls that are interrupted result in tokens billed but no response received (silent cost leak). The engine logs request start (with input token count) before each provider call, so interrupted calls have at minimum an input-cost audit record. Streaming calls are charged only for tokens sent before disconnect.

#### Strategy 2: Immediate Cancel (Future Option)

All agent tasks are cancelled immediately via `task.cancel()`. Fastest shutdown but highest data loss — partial tool side effects, billed-but-lost LLM responses.

#### Strategy 3: Finish Current Tool (Future Option)

Like cooperative timeout, but waits for the current tool invocation to complete even if it exceeds the grace period. Needs per-tool timeout as a backstop for long-running sandboxed execution.

#### Strategy 4: Checkpoint and Stop (Planned — M4/M5)

On shutdown signal, each agent persists its full `AgentContext` snapshot and transitions to `SUSPENDED`. On restart, the engine loads checkpoints and resumes execution. This naturally extends the `CheckpointStrategy` from §6.6 — the only difference is whether the checkpoint was written proactively (graceful shutdown) or loaded from the last turn (crash recovery).

> **Planned non-terminal status:** `SUSPENDED` is a new `TaskStatus` variant for checkpoint-based shutdown, to be added alongside `INTERRUPTED` in M4/M5.

### 6.8 Concurrent Workspace Isolation (M4+)

> **MVP: Not applicable.** M3 is single-agent — no concurrent file edits are possible. This section defines the M4+ strategy for multi-agent workspace coordination.

When multiple agents work on the same codebase concurrently, they may need to edit overlapping files. The framework provides a pluggable `WorkspaceIsolationStrategy` protocol for managing concurrent file access. The default strategy combines intelligent task decomposition with git worktree isolation — the dominant industry pattern (used by OpenAI Codex, Cursor, Claude Code, VS Code background agents).

#### Strategy 1: Planner + Git Worktrees (Default)

The task planner decomposes work to minimize file overlap across agents. Each agent operates in its own git worktree (shared `.git` object database, independent working tree). On completion, branches are merged sequentially.

```text
Planner decomposes task:
├─ Agent A: src/auth/     (worktree-A)
├─ Agent B: src/api/      (worktree-B)
└─ Agent C: tests/        (worktree-C)

Each in isolated git worktree
        │
On completion: sequential merge
├─ Merge A → main
├─ Rebase B on main, merge
└─ Rebase C on main, merge
        │
Textual conflicts: git detects, escalate to human or review agent
Semantic conflicts: review agent evaluates merged result
```

```yaml
workspace_isolation:
  strategy: "planner_worktrees"      # planner_worktrees, sequential, file_locking
  planner_worktrees:
    max_concurrent_worktrees: 8
    merge_order: "completion"        # completion (first done merges first), priority, manual
    conflict_escalation: "human"     # human, review_agent
```

- True filesystem isolation — agents cannot overwrite each other's work
- Maximum parallelism during execution; conflicts deferred to merge time
- Leverages mature git infrastructure for merge, diff, and history

#### Strategy 2: Sequential Dependencies (Future Option)

Tasks with overlapping file scopes are ordered sequentially via a dependency graph. Prevents conflicts by construction but limits parallelism. Requires upfront knowledge of which files a task will touch.

#### Strategy 3: File-Level Locking (Future Option)

Files are locked at task assignment time. Eliminates conflicts at the source but requires predicting file access — difficult for LLM agents that discover what to edit as they go. Risk of deadlock if multiple agents need overlapping file sets.

#### State Coordination vs Workspace Isolation

These are complementary systems handling different types of shared state:

| State Type | Coordination | Mechanism |
|-----------|-------------|-----------|
| Framework state (tasks, assignments, budget) | Centralized single-writer (`TaskEngine`) | `model_copy(update=...)` via async queue |
| Code and files (agent work output) | Workspace isolation (`WorkspaceIsolationStrategy`) | Git worktrees / branches |
| Agent memory (personal) | Per-agent ownership | Each agent owns its memory exclusively |
| Org memory (shared knowledge) | Single-writer (`OrgMemoryBackend`) | `OrgMemoryBackend` protocol with role-based write access control |

### 6.9 Task Decomposability & Coordination Topology (M4+)

> **MVP: Not applicable.** M3 is single-agent. This section defines M4+ concepts for multi-agent task routing.

Empirical research on agent scaling ([Kim et al., 2025](https://arxiv.org/abs/2512.08296) — 180 controlled experiments across 3 LLM families and 4 benchmarks) demonstrates that **task decomposability is the strongest predictor of multi-agent effectiveness** — stronger than team size, model capability, or coordination architecture.

#### Task Structure Classification

Each task will carry a `task_structure` field (to be added to §6.2 Task Definition at M4) classifying its decomposability:

| Structure | Description | MAS Effect | Example |
|-----------|-------------|------------|---------|
| `sequential` | Steps must execute in strict order; each depends on prior state | **Negative** (−39% to −70%) | Multi-step build processes, ordered migrations, chained API calls |
| `parallel` | Sub-problems can be investigated independently, then synthesized | **Positive** (+57% to +81%) | Financial analysis (revenue + cost + market), multi-file review, research across sources |
| `mixed` | Some sub-tasks are parallel, but a sequential backbone connects phases | **Variable** (depends on ratio) | Feature implementation (design ∥ research → implement → test) |

Classification can be:
- **Explicit** — set in task config by the task creator or manager agent
- **Inferred** — derived from task properties (tool count, dependency graph, acceptance criteria structure) by the task router (M4+)

#### Per-Task Coordination Topology (M4+)

The communication pattern (§5.1) is configured at the company level, but **coordination topology can be selected per-task** based on task structure and properties. This allows the engine to use the most efficient coordination approach for each task rather than applying a single company-wide pattern.

| Task Properties | Recommended Topology | Rationale |
|----------------|---------------------|-----------|
| `sequential` + few tools (≤4) | **Single-agent (SAS)** | Coordination overhead fragments reasoning capacity on sequential tasks |
| `parallel` + structured domain | **Centralized** | Orchestrator decomposes, sub-agents execute in parallel, orchestrator synthesizes. Lowest error amplification (4.4×) |
| `parallel` + exploratory/open-ended | **Decentralized** | Peer debate enables diverse exploration of high-entropy search spaces |
| `mixed` | **Context-dependent** | Sequential backbone handled by single agent; parallel sub-tasks delegated to sub-agents |

#### Auto Topology Selector (M4+)

When topology is set to `"auto"`, the engine selects coordination topology based on measurable task properties:

```yaml
coordination:
  topology: "auto"                    # auto, sas, centralized, decentralized, context_dependent
  auto_topology_rules:
    # sequential tasks → always single-agent
    sequential_override: "sas"
    # parallel tasks → select based on domain structure
    parallel_default: "centralized"
    # mixed tasks → SAS backbone for sequential phases, delegates parallel sub-tasks
    mixed_default: "context_dependent"  # hybrid: not a single topology — engine selects per-phase
```

The auto-selector uses task structure, tool count, and (when available from M5 memory) historical single-agent success rate as inputs. The exact selection logic is an M4 implementation detail — the spec defines the interface and the empirically-grounded heuristics above.

> **Reference:** These heuristics are derived from Kim et al. (2025), which achieved 87% accuracy predicting optimal architecture from task properties across held-out configurations. Our context differs (role-differentiated agents vs. identical agents), so thresholds should be validated empirically once multi-agent execution is implemented.

---

## 7. Memory & Persistence

### 7.1 Memory Architecture

```text
┌─────────────────────────────────────────────┐
│              Agent Memory System             │
├──────────┬──────────┬───────────┬───────────┤
│ Working  │ Episodic │ Semantic  │Procedural │
│ Memory   │ Memory   │ Memory    │ Memory    │
│          │          │           │           │
│ Current  │ Past     │ Knowledge │ Skills &  │
│ task     │ events & │ & facts   │ how-to    │
│ context  │ decisions│ learned   │           │
├──────────┴──────────┴───────────┴───────────┤
│            Storage Backend                   │
│   SQLite / PostgreSQL / File-based           │
│   + Memory Layer (TBD — see §15.2)           │
└─────────────────────────────────────────────┘
```

### 7.2 Memory Types

| Type | Scope | Persistence | Example |
|------|-------|-------------|---------|
| **Working** | Current task | None (in-context) | "I'm implementing the auth endpoint" |
| **Episodic** | Past events | Configurable | "Last sprint we chose JWT over sessions" |
| **Semantic** | Knowledge | Long-term | "This project uses FastAPI with SQLAlchemy" |
| **Procedural** | Skills/patterns | Long-term | "Code reviews require 2 approvals here" |
| **Social** | Relationships | Long-term | "The QA lead prefers detailed test plans" |

### 7.3 Memory Levels (Configurable)

```yaml
memory:
  level: "full"                 # none, session, project, full
  backend: "sqlite"             # sqlite, postgresql, file (memory layer library is on top, not a backend itself — see §15.2)
  options:
    retention_days: null         # null = forever
    max_memories_per_agent: 10000
    consolidation_interval: "daily"  # compress old memories
    shared_knowledge_base: true      # agents can access shared facts (see §7.4)
```

### 7.4 Shared Organizational Memory

Beyond individual agent memory (§7.1–7.3), the framework needs **organizational memory** — company-wide knowledge that all agents can access: policies, conventions, architecture decision records (ADRs), coding standards, and operational procedures. This is not personal episodic memory ("what I did last Tuesday") but institutional knowledge ("we always use FastAPI, not Flask").

Shared organizational memory is implemented behind an `OrgMemoryBackend` protocol, making the system highly modular and extensible. New backends can be added without modifying existing ones.

#### Backend 1: Hybrid Prompt + Retrieval (Default / MVP)

Critical rules (5-10 items, e.g., "no commits to main," "all PRs need 2 approvals") are injected into every agent's system prompt. Extended knowledge (ADRs, detailed procedures, style guides) is stored in a queryable store and retrieved on demand at task start.

```yaml
org_memory:
  backend: "hybrid_prompt_retrieval"    # hybrid_prompt_retrieval, graph_rag, temporal_kg
  core_policies:                        # always in system prompt
    - "All code must have 80%+ test coverage"
    - "Use FastAPI, not Flask"
    - "PRs require 2 approvals"
  extended_store:
    backend: "sqlite"                   # sqlite, postgresql
    max_retrieved_per_query: 5
  write_access:
    policies: ["human"]                 # only humans write core policies
    adrs: ["human", "senior", "lead", "c_suite"]
    procedures: ["human", "senior", "lead", "c_suite"]
```

- Simple to implement. Core rules always present. Extended knowledge scales
- Basic retrieval may miss relational connections between policies

#### Research Directions (M5+)

The following backends illustrate why `OrgMemoryBackend` is a protocol — the architecture supports future upgrades without modifying existing code. These are **not planned implementations**; they are research directions that may inform future work if/when organizational memory needs outgrow the Hybrid Prompt + Retrieval approach.

#### Backend 2: GraphRAG Knowledge Graph (Research)

Organizational knowledge stored as entities + relationships in a knowledge graph. Agents query via graph traversal, enabling multi-hop reasoning: "FastAPI is our standard" → linked to → "don't use Flask" → linked to → "exception: data team uses Django for admin."

```yaml
org_memory:
  backend: "graph_rag"
  graph:
    store: "sqlite"                     # graph stored in relational DB, or dedicated graph DB
    entity_extraction: "auto"           # auto-extract entities from ADRs and policies
```

- Significant accuracy improvement over vector-only retrieval (some benchmarks report 3–4x gains). Multi-hop reasoning captures policy relationships
- More complex infrastructure. Entity extraction can be noisy. Heavier setup

#### Backend 3: Temporal Knowledge Graph (Research)

Like GraphRAG but tracks how facts change over time. "We used Flask until March 2026, then switched to FastAPI." Agents see current truth but can query history for context.

```yaml
org_memory:
  backend: "temporal_kg"
  temporal:
    track_changes: true
    history_retention_days: null        # null = forever
```

- Handles policy evolution naturally. Agents understand when and why things changed
- Most complex. Potentially overkill for small companies or local-first use

> **Extensibility:** All backends implement the `OrgMemoryBackend` protocol (`query(context) → list[OrgFact]`, `write(fact, author)`, `list_policies()`). The MVP ships with Backend 1; Backends 2 and 3 are research directions that may be explored if the default approach proves insufficient. The memory layer candidate (currently evaluating Mem0 and alternatives — see §15.2) may provide graph memory capabilities natively, reducing implementation effort for Backends 2-3.
> **Write access control:** Core policies are human-only. ADRs and procedures can be written by senior+ agents. All writes are versioned and auditable. This prevents agents from corrupting shared organizational knowledge while allowing senior agents to document decisions.

---

## 8. HR & Workforce Management

> **MVP: Not in M3–M4.** HR features (hiring, firing, performance tracking, promotions) are M5–M7. Agent workforce is configured manually via YAML in early milestones.

### 8.1 Hiring Process

The HR system manages the agent workforce dynamically:

1. HR agent (or human) identifies skill gap or workload issue
2. HR generates **candidate cards** based on team needs:
   - What skills are underrepresented?
   - What seniority level is needed?
   - What personality would complement the team?
   - What model/provider fits the budget?
3. Candidate cards are presented for approval (to CEO or human)
4. Approved candidates are instantiated and onboarded
5. Onboarding includes: company context, project briefing, team introductions.

### 8.2 Firing / Offboarding

1. Triggered by: budget cuts, poor performance metrics, project completion, human decision
2. Agent's memory is archived (not deleted)
3. Active tasks are reassigned
4. Team is notified

### 8.3 Performance Tracking

```yaml
agent_metrics:
  tasks_completed: 42
  tasks_failed: 2
  average_quality_score: 8.5     # from code reviews, peer feedback
  average_cost_per_task: 0.45
  average_completion_time: "2h"
  collaboration_score: 7.8       # peer ratings
  last_review_date: "2026-02-20"
```

### 8.4 Promotions & Demotions

Agents can move between seniority levels based on performance:
- Promotion criteria: sustained high quality scores, task complexity handled, peer feedback
- Demotion criteria: repeated failures, quality drops, cost inefficiency
- Promotions can unlock higher tool access levels (see Progressive Trust)
- Model upgrades/downgrades may accompany level changes (configurable)

---

## 9. Model Provider Layer

### 9.1 Provider Abstraction

```text
┌─────────────────────────────────────────────┐
│            Unified Model Interface            │
│   completion(messages, tools, config) → resp  │
├───────────┬───────────┬───────────┬─────────┤
│ Cloud API │OpenRouter │  Ollama   │ Custom  │
│  Adapter  │  Adapter  │  Adapter  │ Adapter │
├───────────┼───────────┼───────────┼─────────┤
│ Direct    │ 400+ LLMs│ Local LLMs│ Any API │
│ API call  │ via OR    │ Self-host │         │
└───────────┴───────────┴───────────┴─────────┘
```

### 9.2 Provider Configuration

> Note: Model IDs, pricing, and provider examples below are **illustrative**. Actual models, costs, and provider availability will be determined during implementation and should be loaded dynamically from provider APIs where possible.

```yaml
providers:
  example-provider:
    api_key: "${PROVIDER_API_KEY}"
    models:                        # example entries — real list loaded from provider
      - id: "example-large-001"
        alias: "large"
        cost_per_1k_input: 0.015   # illustrative, verify at implementation time
        cost_per_1k_output: 0.075
        max_context: 200000
        estimated_latency_ms: 1500 # optional, used by fastest strategy
      - id: "example-medium-001"
        alias: "medium"
        cost_per_1k_input: 0.003
        cost_per_1k_output: 0.015
        max_context: 200000
        estimated_latency_ms: 500
      - id: "example-small-001"
        alias: "small"
        cost_per_1k_input: 0.0008
        cost_per_1k_output: 0.004
        max_context: 200000
        estimated_latency_ms: 200

  openrouter:
    api_key: "${OPENROUTER_API_KEY}"
    base_url: "https://openrouter.ai/api/v1"
    models:                        # example entries
      - id: "vendor-a/model-medium"
        alias: "or-medium"
      - id: "vendor-b/model-pro"
        alias: "or-pro"
      - id: "vendor-c/model-reasoning"
        alias: "or-reasoning"

  ollama:
    base_url: "http://localhost:11434"
    models:                        # example entries
      - id: "llama3.3:70b"
        alias: "local-llama"
        cost_per_1k_input: 0.0    # free, local
        cost_per_1k_output: 0.0
      - id: "qwen2.5-coder:32b"
        alias: "local-coder"
        cost_per_1k_input: 0.0
        cost_per_1k_output: 0.0
```

### 9.3 LiteLLM Integration (Candidate)

Use **LiteLLM** as the provider abstraction layer:
- Unified API across 100+ providers
- Built-in cost tracking
- Automatic retries and fallbacks
- Load balancing across providers
- OpenAI-compatible interface (all providers normalized)

### 9.4 Model Routing Strategy

```yaml
routing:
  strategy: "smart"              # smart, cheapest, fastest, role_based, cost_aware, manual
  # Strategy behaviors:
  #   manual      — resolve an explicit model override; fails if not set
  #   role_based  — match agent seniority level to routing rules, then catalog default
  #   cost_aware  — match task-type rules, then pick cheapest model within budget
  #   cheapest    — alias for cost_aware
  #   fastest     — match task-type rules, then pick fastest model (by estimated_latency_ms)
  #                 within budget; falls back to cheapest when no latency data is available
  #   smart       — priority cascade: override > task-type > role > seniority > cheapest > fallback chain
  rules:
    - role_level: "C-Suite"
      preferred_model: "large"
      fallback: "medium"
    - role_level: "Senior"
      preferred_model: "medium"
      fallback: "small"
    - role_level: "Junior"
      preferred_model: "small"
      fallback: "local-small"
    - task_type: "code_review"
      preferred_model: "medium"
    - task_type: "documentation"
      preferred_model: "small"
    - task_type: "architecture"
      preferred_model: "large"
  fallback_chain:
    - "example-provider"
    - "openrouter"
    - "ollama"
```

---

## 10. Cost & Budget Management

### 10.1 Budget Hierarchy

```text
Company Budget ($100/month)
  ├── Engineering Dept (50%) ── $50
  │     ├── Backend Team (40%) ── $20
  │     ├── Frontend Team (30%) ── $15
  │     └── DevOps Team (30%) ── $15
  ├── Quality/QA (10%) ── $10
  ├── Product Dept (15%) ── $15
  ├── Operations (10%) ── $10
  └── Reserve (15%) ── $15
```

> Note: Percentages are illustrative defaults. All allocations are configurable per company.

### 10.2 Cost Tracking

Every API call is tracked (illustrative schema):

```json
{
  "agent_id": "sarah_chen",
  "task_id": "task-123",
  "provider": "example-provider",
  "model": "example-medium-001",
  "input_tokens": 4500,
  "output_tokens": 1200,
  "cost_usd": 0.0315,
  "timestamp": "2026-02-27T10:30:00Z"
}
```

> **Implementation note:** `CostRecord` stores `input_tokens` and `output_tokens`; `total_tokens` is not stored on `CostRecord` — it is a `@computed_field` property on `TokenUsage` (the model embedded in `CompletionResponse`). `_SpendingTotals` base class provides shared `total_cost_usd`, `total_input_tokens`, `total_output_tokens`, and `record_count` fields. `AgentSpending`, `DepartmentSpending`, and `PeriodSpending` extend it with their dimension-specific fields.

### 10.3 CFO Agent Responsibilities

> **MVP: Not in M3.** Budget tracking and per-task cost recording exist (M2), but the CFO agent is M5+. Cost controls (§10.4) are enforced by the engine, not by an agent.

The CFO agent (when enabled) acts as a cost management system:

- Monitors real-time spending across all agents
- Alerts when departments approach budget limits
- Suggests model downgrades when budget is tight
- Reports daily/weekly spending summaries
- Recommends hiring/firing based on cost efficiency
- Blocks tasks that would exceed remaining budget
- Optimizes model routing for cost/quality balance

### 10.4 Cost Controls

> **Minimal config:**
>
> ```yaml
> budget:
>   total_monthly: 100.00
> ```
>
> All other fields below have sensible defaults.

```yaml
budget:
  total_monthly: 100.00
  alerts:
    warn_at: 75               # percent
    critical_at: 90
    hard_stop_at: 100
  per_task_limit: 5.00
  per_agent_daily_limit: 10.00
  auto_downgrade:
    enabled: true
    threshold: 85              # percent of budget used
    boundary: "task_assignment" # task_assignment only — NEVER mid-execution
    downgrade_map:             # ordered pairs — aliases reference configured models
      - ["large", "medium"]
      - ["medium", "small"]
      - ["small", "local-small"]
```

> **Auto-downgrade boundary:** Model downgrades apply only at **task assignment time**, never mid-execution. An agent halfway through an architecture review cannot be switched to a cheaper model — the task completes on its assigned model. The next task assignment respects the downgrade threshold. This prevents quality degradation from mid-thought model switches.

### 10.5 LLM Call Analytics

> **MVP: Proxy metrics only (M3).** Call categorization is M4. Full analytics layer is M5+.

Every LLM provider call is tracked with comprehensive metadata for financial reporting, debugging, and orchestration overhead analysis. The analytics system builds incrementally across milestones.

#### M3: Per-Call Tracking + Proxy Overhead Metrics

Every completion call produces a `CompletionResponse` with `TokenUsage` (token counts and cost). The engine layer creates a `CostRecord` (with agent/task context) and records it into `CostTracker` — the provider itself does not have agent/task context. In M3, the engine additionally logs **proxy overhead metrics** at task completion:

- `turns_per_task` — number of LLM turns to complete the task (from `AgentRunResult.total_turns`)
- `tokens_per_task` — total tokens consumed (from `AgentContext.accumulated_cost.total_tokens`)
- `cost_per_task` — total USD cost (from `AgentContext.accumulated_cost.cost_usd` via `AgentRunResult.total_cost_usd`)
- `duration_seconds` — wall-clock execution time in seconds (from `AgentRunResult.duration_seconds`)

These are natural overhead indicators — a task consuming 15 turns and 50k tokens for a one-line fix signals a problem.

These metrics are captured in `TaskCompletionMetrics` (in `engine/metrics.py`), a frozen Pydantic model with a `from_run_result()` factory method. The engine logs these metrics at task completion via the `EXECUTION_ENGINE_TASK_METRICS` event.

#### M4: Call Categorization + Orchestration Ratio

When multi-agent coordination exists, each `CostRecord` is tagged with a **call category**:

| Category | Description | Examples |
|----------|-------------|---------|
| `productive` | Direct task work — tool calls, code generation, task output | Agent writing code, running tests |
| `coordination` | Inter-agent communication — delegation, reviews, meetings | Manager reviewing work, agent presenting in meeting |
| `system` | Framework overhead — system prompt injection, context loading | Initial prompt, memory retrieval injection |

The **orchestration ratio** (`coordination / total`) is surfaced in metrics and alerts. If coordination tokens consistently exceed productive tokens, the company configuration needs tuning (fewer approval layers, simpler meeting protocols, etc.).

#### M4: Coordination Metrics Suite

Beyond call categorization and orchestration ratio, M4 introduces a comprehensive suite of coordination metrics derived from empirical agent scaling research ([Kim et al., 2025](https://arxiv.org/abs/2512.08296)). These metrics explain coordination dynamics and enable data-driven tuning of multi-agent configurations.

| Metric | Symbol | Definition | What It Signals |
|--------|--------|------------|-----------------|
| **Coordination efficiency** | `Ec` | `success_rate / (turns / turns_sas)` — success normalized by relative turn count vs single-agent baseline | Overall coordination ROI. Low Ec = coordination costs exceed benefits |
| **Coordination overhead** | `O%` | `(turns_mas - turns_sas) / turns_sas × 100%` — relative turn increase | Communication cost. Optimal band: 200–300%. Above 400% = over-coordination |
| **Error amplification** | `Ae` | `error_rate_mas / error_rate_sas` — relative failure probability | Whether MAS corrects or propagates errors. Centralized ≈ 4.4×, Independent ≈ 17.2× |
| **Message density** | `c` | Inter-agent messages per reasoning turn | Communication intensity. Performance saturates at ≈ 0.39 messages/turn |
| **Redundancy rate** | `R` | Mean cosine similarity of agent output embeddings | Agent agreement. Optimal at ≈ 0.41 (balances fusion with independence) |

> **Configurable collection:** All 5 metrics are opt-in via `coordination_metrics.enabled` in analytics config. `Ec` and `O%` are cheap (turn counting). `Ae` requires baseline comparison data. `c` and `R` require semantic analysis of agent outputs (embedding computation). Enable selectively based on data-gathering needs.

```yaml
coordination_metrics:
  enabled: false                       # opt-in — enable for data gathering
  collect:
    - efficiency                       # cheap — turn counting
    - overhead                         # cheap — turn counting
    - error_amplification              # requires SAS baseline data
    - message_density                  # requires message counting infrastructure
    - redundancy                       # requires embedding computation on outputs
  baseline_window: 50                  # number of SAS runs to establish baseline for Ae
  error_taxonomy:
    enabled: false                     # opt-in — enable for targeted diagnosis
    categories:
      - logical_contradiction
      - numerical_drift
      - context_omission
      - coordination_failure
```

#### M5+: Full Analytics Layer

Expanded per-call metadata for comprehensive financial and operational reporting:

```yaml
call_analytics:
  track:
    - call_category                    # productive, coordination, system
    - success                          # true/false
    - retry_count                      # 0 = first attempt succeeded
    - retry_reason                     # rate_limit, timeout, internal_error
    - latency_ms                       # wall-clock time for the call (not estimated_latency_ms from config)
    - finish_reason                    # stop, tool_use, max_tokens, error
    - cache_hit                        # prompt caching hit/miss (provider-dependent)
  aggregation:
    - per_agent_daily                  # agent spending over time
    - per_task                         # total cost per task
    - per_department                   # department-level rollups
    - per_provider                     # provider reliability and cost comparison
    - orchestration_ratio              # coordination vs productive tokens
  alerts:
    orchestration_ratio:
      info: 0.30                       # info if coordination > 30% of total
      warn: 0.50                       # warn if coordination > 50% of total
      critical: 0.70                   # critical if coordination > 70% of total
    retry_rate_warn: 0.1               # warn if > 10% of calls need retries
```

> **Design principle:** Analytics metadata is append-only and never blocks execution. Failed analytics writes are logged and skipped — the agent's task is never delayed by telemetry. All analytics data flows through the existing `CostRecord` and structured logging infrastructure.

#### M4/M5: Coordination Error Taxonomy

When coordination metrics collection is enabled, the system can optionally classify coordination errors into structured categories. This enables targeted diagnosis — e.g., if coordination failures spike, the topology may be too complex; if context omissions spike, the orchestrator's synthesis is insufficient.

| Error Category | Description | Detection Method |
|---------------|-------------|-----------------|
| **Logical contradiction** | Agent asserts both "X is true" and "X is false", or derives conclusions violating its stated premises | Semantic contradiction detection on agent outputs |
| **Numerical drift** | Accumulated computational errors from cascading rounding or unit conversion (>5% deviation) | Numerical comparison against ground truth or cross-agent verification |
| **Context omission** | Failure to reference previously established entities, relationships, or state required for current reasoning | Missing-reference detection across agent conversation history |
| **Coordination failure** | MAS-specific: message misinterpretation, task allocation conflicts, state synchronization errors between agents | Protocol-level error detection in orchestration layer |

> **Configurable and opt-in:** Error taxonomy classification requires semantic analysis of agent outputs and is expensive. Enable via `coordination_metrics.error_taxonomy.enabled: true` only when actively gathering data for system tuning. The classification pipeline runs post-execution (never blocks agent work) and logs structured events to the observability layer. This configuration is part of the main `coordination_metrics` block defined in the Coordination Metrics Suite section above.

> **Reference:** Error categories derived from [Kim et al., 2025](https://arxiv.org/abs/2512.08296) and the Multi-Agent System Failure Taxonomy (MAST) by Cemri et al. (2025). Architecture-specific patterns: centralized coordination reduces logical contradictions by 36.4% and context omissions by 66.8% via orchestrator synthesis; hybrid topology introduces 12.4% coordination failures due to protocol complexity.

---

## 11. Tool & Capability System

### 11.1 Tool Categories

| Category | Tools | Typical Roles |
|----------|-------|---------------|
| **File System** | Read, write, edit, delete files | All developers, writers |
| **Code Execution** | Run code in sandboxed environments | Developers, QA |
| **Version Control** | Git operations, PR management | Developers, DevOps |
| **Web** | HTTP requests, web scraping, search | Researchers, analysts |
| **Database** | Query, migrate, admin | Backend devs, DBAs |
| **Terminal** | Shell commands (sandboxed) | DevOps, senior devs |
| **Design** | Image generation, mockup tools | Designers |
| **Communication** | Email, Slack, notifications | PMs, executives |
| **Analytics** | Metrics, dashboards, reporting | Data analysts, CFO |
| **Deployment** | CI/CD, container management | DevOps, SRE |
| **MCP Servers** | Any MCP-compatible tool | Configurable per agent |

### 11.1.1 Tool Execution Model

When the LLM requests multiple tool calls in a single turn, `ToolInvoker.invoke_all` executes them **concurrently** using `asyncio.TaskGroup`. An optional `max_concurrency` parameter (default unbounded) limits parallelism via `asyncio.Semaphore`. Recoverable errors are captured as `ToolResult(is_error=True)` without aborting sibling invocations; non-recoverable errors (`MemoryError`, `RecursionError`) are collected and re-raised after all tasks complete (bare exception for one, `ExceptionGroup` for multiple).

`BaseTool.parameters_schema` deep-copies the caller-supplied schema at construction and wraps it in `MappingProxyType` for read-only enforcement; the property returns a deep copy on access to prevent mutation of internal state. `ToolInvoker` deep-copies arguments at the tool execution boundary before passing them to `tool.execute()`. `MappingProxyType` wrapping is also used in `ToolRegistry` for its internal collections.

### 11.1.2 Tool Sandboxing

Tool execution requires safety boundaries proportional to the risk of each tool category. The framework uses a **layered sandboxing strategy** with a pluggable `SandboxBackend` protocol — new backends can be added without modifying existing ones. The default configuration uses lighter isolation for low-risk tools and stronger isolation for high-risk tools.

> **MVP: Subprocess sandbox for file/git tools. Docker optional for code execution.** K8s is future.

#### Sandbox Backends

| Backend | Isolation | Latency | Dependencies | Status |
|---------|-----------|---------|--------------|--------|
| `SubprocessSandbox` | Process-level: timeout, restricted PATH, workspace-scoped paths | ~ms | None | M3 |
| `DockerSandbox` | Container-level: ephemeral container, mounted workspace, no network, resource limits (CPU/memory/time) | ~1-2s cold start | Docker | M3 |
| `K8sSandbox` | Pod-level: per-agent containers, namespace isolation, resource quotas, network policies | ~2-5s | Kubernetes | Future |

#### Default Layered Configuration

```yaml
sandboxing:
  default_backend: "subprocess"        # subprocess, docker, k8s
  overrides:                           # per-category backend overrides
    file_system: "subprocess"          # low risk — fast, no deps
    git: "subprocess"                  # low risk — workspace-scoped
    web: "docker"                      # medium risk — needs network isolation
    code_execution: "docker"           # high risk — strong isolation required
    terminal: "docker"                 # high risk — arbitrary commands
    database: "docker"                 # high risk — data mutation; see network note below
  subprocess:
    timeout_seconds: 30
    workspace_only: true               # restrict filesystem access to project dir
    restricted_path: true              # strip dangerous binaries from PATH
  docker:
    image: "ai-company-sandbox:latest" # pre-built image with common runtimes
    network: "none"                    # no network by default; per-category overrides below
    network_overrides:                 # category-specific network policies
      database: "bridge"               # database tools need TCP access to DB host
      web: "egress-only"               # web tools need outbound HTTP; no inbound
    allowed_hosts: []                  # allowlist of host:port pairs (e.g. ["db:5432"])
    memory_limit: "512m"
    cpu_limit: "1.0"
    timeout_seconds: 120
    mount_mode: "rw"                   # rw for workspace dir, nothing else mounted
    auto_remove: true                  # ephemeral — container removed after execution
  k8s:                                 # future — per-agent pod isolation
    namespace: "ai-company-agents"
    resource_requests:
      cpu: "250m"
      memory: "256Mi"
    resource_limits:
      cpu: "1"
      memory: "1Gi"
    network_policy: "deny-all"         # default deny, allowlist per tool
```

> **User experience:** Docker is optional — only required when code execution, terminal, web, or database tools are enabled. File system and git tools work out of the box with subprocess isolation. This keeps the "local first" experience lightweight while providing strong isolation where it matters.

> **Scaling path:** In a future Kubernetes deployment (§18.2 Phase 3-4), each agent can run in its own pod via `K8sSandbox`. At that point, the layered configuration becomes less relevant — all tools execute within the agent's isolated pod. The `SandboxBackend` protocol makes this transition seamless.

### 11.2 Tool Access Levels

```yaml
tool_access:
  levels:
    sandboxed:
      description: "No external access. Isolated workspace."
      file_system: "workspace_only"
      code_execution: "containerized"
      network: "none"
      git: "local_only"

    restricted:
      description: "Limited external access with approval."
      file_system: "project_directory"
      code_execution: "containerized"
      network: "allowlist_only"
      git: "read_and_branch"
      requires_approval: ["deployment", "database_write"]

    standard:
      description: "Normal development access."
      file_system: "project_directory"
      code_execution: "containerized"
      network: "open"
      git: "full"
      terminal: "restricted_commands"

    elevated:
      description: "Full access for senior/trusted agents."
      file_system: "full"
      code_execution: "host"
      network: "open"
      git: "full"
      terminal: "full"
      deployment: true

    custom:
      description: "Per-agent custom configuration."
```

### 11.3 Progressive Trust

Agents can earn higher tool access over time through configurable trust strategies. The trust system implements a `TrustStrategy` protocol, making it extensible. Multiple strategies are available, selectable via config.

> **MVP: Disabled (static access).** Agents receive their configured access level at hire time — no automated trust evolution until M7. The `TrustStrategy` protocol ensures all strategies are pluggable.
>
> **Security invariant (all strategies):** The `standard_to_elevated` promotion **always** requires human approval. No agent can auto-gain production access regardless of trust strategy.

#### Strategy: Disabled (Static Access) — Default

Trust is disabled. Agents receive their configured access level at hire time and it never changes. Simplest option — useful when the human manages permissions manually.

```yaml
trust:
  strategy: "disabled"               # disabled, weighted, per_category, milestone
  initial_level: "standard"          # fixed access level for all agents
```

#### Strategy: Weighted Score (Single Track)

A single trust score computed from weighted factors: task difficulty completed, error rate, time active, and human feedback. One global trust level per agent, applied to all tool categories.

```yaml
trust:
  strategy: "weighted"
  initial_level: "sandboxed"
  weights:
    task_difficulty: 0.3             # harder tasks completed = more trust
    completion_rate: 0.25
    error_rate: 0.25                 # inverse — fewer errors = more trust
    human_feedback: 0.2
  promotion_thresholds:
    sandboxed_to_restricted: 0.4
    restricted_to_standard: 0.6
    standard_to_elevated:
      score: 0.8
      requires_human_approval: true  # always human-gated
```

- Simple model, easy to understand. One number to track
- Too coarse — an agent trusted for file edits shouldn't auto-get deployment access

#### Strategy: Per-Category Trust Tracks

Separate trust tracks per tool category (filesystem, git, deployment, database, network). An agent can be "standard" for files but "sandboxed" for deployment. Promotion criteria differ per category. Human approval gate required for any production-touching category.

```yaml
trust:
  strategy: "per_category"
  initial_levels:
    file_system: "restricted"
    git: "restricted"
    code_execution: "sandboxed"
    deployment: "sandboxed"
    database: "sandboxed"
    terminal: "sandboxed"
  promotion_criteria:
    file_system:
      restricted_to_standard:
        tasks_completed: 10
        quality_score_min: 7.0
    deployment:
      sandboxed_to_restricted:
        tasks_completed: 20
        quality_score_min: 8.5
        requires_human_approval: true  # always human-gated for deployment
```

- Granular. Matches real security models (IAM roles). Prevents gaming via easy tasks
- More complex data model. Trust state is a matrix per agent, not a scalar

#### Strategy: Milestone Gates (ATF-Inspired)

Explicit capability milestones aligned with the Cloud Security Alliance Agentic Trust Framework. Automated promotion for low-risk levels. Human approval gates for elevated access. Trust is time-bound and subject to periodic re-verification — trust decays if the agent is idle for extended periods or error rate increases.

```yaml
trust:
  strategy: "milestone"
  initial_level: "sandboxed"
  milestones:
    sandboxed_to_restricted:
      tasks_completed: 5
      quality_score_min: 7.0
      auto_promote: true             # no human needed
    restricted_to_standard:
      tasks_completed: 20
      quality_score_min: 8.0
      time_active_days: 7
      auto_promote: true
    standard_to_elevated:
      requires_human_approval: true  # always human-gated
      clean_history_days: 14         # no errors in last 14 days
  re_verification:
    enabled: true
    interval_days: 90                # re-verify every 90 days
    decay_on_idle_days: 30           # demote one level if idle 30+ days
    decay_on_error_rate: 0.15        # demote if error rate exceeds 15%
```

- Industry-aligned. Re-verification prevents stale trust. Human gates where it matters
- Most complex. Trust decay may need tuning to avoid frustrating users

---

## 12. Security & Approval System

### 12.1 Approval Workflow

```text
                    ┌──────────────┐
                    │  Task/Action  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ Security Ops  │
                    │   Agent       │
                    └──────┬───────┘
                     ╱           ╲
              ┌─────▼─┐      ┌───▼────┐
              │APPROVE │      │ DENY   │
              │(auto)  │      │+ reason│
              └────┬───┘      └───┬────┘
                   │              │
              Execute         ┌───▼────────┐
                              │ Human Queue │
                              │ (Dashboard) │
                              └───┬────────┘
                            ╱         ╲
                     ┌─────▼─┐    ┌───▼──────┐
                     │Override│    │Alternative│
                     │Approve │    │Suggested  │
                     └────────┘    └──────────┘
```

### 12.2 Autonomy Levels

> **Planned minimal config (not yet implemented — current schema uses a float):**
>
> ```yaml
> autonomy:
>   level: "semi"
> ```
>
> All presets below are built-in. Most users only set the level.

```yaml
autonomy:
  level: "semi"                  # full, semi, supervised, locked
  presets:
    full:
      description: "Agents work independently. Human notified of results only."
      auto_approve: ["all"]
      human_approval: []

    semi:
      description: "Most work is autonomous. Major decisions need approval."
      auto_approve: ["code_changes", "tests", "docs", "internal_comms"]
      human_approval: ["deployment", "external_comms", "budget_over_threshold", "hiring"]
      security_agent: true

    supervised:
      description: "Human approves major steps. Agents handle details."
      auto_approve: ["file_edits", "internal_comms"]
      human_approval: ["architecture", "new_files", "deployment", "git_push"]
      security_agent: true

    locked:
      description: "Human must approve every action."
      auto_approve: []
      human_approval: ["all"]
      security_agent: true        # still runs for audit logging, but human is approval authority
```

### 12.3 Security Operations Agent

A special meta-agent that reviews all actions before execution:

- Evaluates safety of proposed actions
- Checks for data leaks, credential exposure, destructive operations
- Validates actions against company policies
- Maintains an audit log of all approvals/denials
- Escalates uncertain cases to human queue with explanation
- **Cannot be overridden by other agents** (only human can override)

### 12.4 Approval Timeout Policy

When an action requires human approval (per autonomy level in §12.2), the agent must wait. The framework provides configurable timeout policies that determine what happens when a human doesn't respond. All policies implement a `TimeoutPolicy` protocol. The policy is configurable per autonomy level and per action risk tier.

> **MVP: Wait Forever only (Policy 1).** Other timeout policies are M5+.

During any wait — regardless of policy — the agent **parks** the blocked task (saving its full serialized `AgentContext` state: conversation, progress, accumulated cost, turn count — i.e., the complete persisted context, distinct from the compact `AgentContextSnapshot` used for telemetry) and picks up other available tasks from its queue. When approval eventually arrives, the agent **resumes** the original context exactly where it left off. This mirrors real company behavior: a junior developer starts another task while waiting for a code review, then returns to the original work when feedback arrives.

#### Policy 1: Wait Forever (Default for Critical Actions)

The action stays in the human queue indefinitely. No timeout, no auto-resolution. The agent is aware the task is parked awaiting approval and works on other tasks in the meantime.

```yaml
approval_timeout:
  policy: "wait"                     # wait, deny, tiered, escalation
```

- Safest — no risk of unauthorized actions. Mirrors "awaiting review" in real workflows
- Can stall tasks indefinitely if human is unavailable. Queue can grow unbounded

#### Policy 2: Deny on Timeout

All unapproved actions auto-deny after a configurable timeout. The agent receives a denial reason ("approval timeout — human did not respond within window") and can retry with a different approach or escalate explicitly.

```yaml
approval_timeout:
  policy: "deny"
  timeout_minutes: 240               # 4 hours
```

- Industry consensus default ("fail closed"). Agent learns to prefer auto-approvable paths
- May stall legitimate work if human is consistently slow

#### Policy 3: Tiered Timeout

Different timeout behavior based on action risk level. Low-risk actions auto-approve after a short wait. Medium-risk actions auto-deny. High-risk/security-critical actions wait forever.

```yaml
approval_timeout:
  policy: "tiered"
  tiers:
    low_risk:
      timeout_minutes: 60
      on_timeout: "approve"          # auto-approve low-risk after 1 hour
      actions: ["file_edits", "internal_comms", "tests"]
    medium_risk:
      timeout_minutes: 240
      on_timeout: "deny"             # auto-deny medium-risk after 4 hours
      actions: ["new_files", "git_push", "architecture"]
    high_risk:
      timeout_minutes: null          # wait forever
      on_timeout: "wait"
      actions: ["deployment", "database_admin", "external_comms", "hiring"]
```

- Pragmatic — low-risk stuff doesn't stall, critical stuff stays safe
- Auto-approve on timeout carries risk. Tuning tier boundaries requires experience

#### Policy 4: Escalation Chain

On timeout, the approval request escalates to the next human in a configured chain (e.g., primary reviewer → manager → VP → board). If the entire chain times out, the action is denied.

```yaml
approval_timeout:
  policy: "escalation"
  chain:
    - role: "direct_manager"
      timeout_minutes: 120
    - role: "department_head"
      timeout_minutes: 240
    - role: "ceo_or_board"
      timeout_minutes: 480
  on_chain_exhausted: "deny"         # deny if entire chain times out
```

- Mirrors real orgs — if your boss is out, their boss covers. Multiple chances for approval
- Requires configuring an escalation chain. More humans involved. Complex to implement

> **Task Suspension and Resumption:** The park/resume mechanism relies on `AgentContext` snapshots (frozen Pydantic models). When a task is parked, the full context is persisted. When approval arrives, the framework loads the snapshot, restores the agent's conversation and state, and resumes execution from the exact point of suspension. This works naturally with the `model_copy(update=...)` immutability pattern — the snapshot is a complete, self-contained state.

---

## 13. Human Interaction Layer

### 13.1 Architecture: API-First

```text
┌─────────────────────────────────────────────┐
│               AI Company Engine              │
│  (Core Logic, Agent Orchestration, Tasks)    │
└──────────────────┬──────────────────────────┘
                   │
          ┌────────▼────────┐
          │   REST/WS API    │
          │   (FastAPI)      │
          └───┬─────────┬───┘
              │         │
      ┌───────▼──┐  ┌───▼────────┐
      │  Web UI   │  │  CLI Tool   │
      │ (Local)   │  │             │
      └──────────┘  └────────────┘
```

### 13.2 API Surface

```text
/api/v1/
  ├── /company          # CRUD company config
  ├── /agents           # List, hire, fire, modify agents
  ├── /departments      # Department management
  ├── /projects         # Project CRUD
  ├── /tasks            # Task management
  ├── /messages         # Communication log
  ├── /meetings         # Schedule, view meeting outputs
  ├── /artifacts        # Browse produced artifacts (code, docs, etc.)
  ├── /budget           # Spending, limits, projections
  ├── /approvals        # Pending human approvals queue
  ├── /analytics        # Performance metrics, dashboards
  ├── /providers        # Model provider status, config
  └── /ws               # WebSocket for real-time updates
```

### 13.3 Web UI Features

- **Dashboard**: Real-time company overview, active tasks, spending
- **Org Chart**: Visual hierarchy, click to inspect any agent
- **Task Board**: Kanban/list view of all tasks across projects
- **Message Feed**: Real-time feed of agent communications
- **Approval Queue**: Pending approvals with context and recommendations
- **Agent Profiles**: Detailed view of each agent's identity, history, metrics
- **Budget Panel**: Spending charts, projections, alerts
- **Meeting Logs**: Transcripts and outcomes of all agent meetings
- **Artifact Browser**: Browse and inspect all produced work
- **Settings**: Company config, autonomy levels, provider settings

### 13.4 Human Roles

The human can interact as:

| Role | Access | Description |
|------|--------|-------------|
| **Board Member** | Observe + major approvals only | Minimal involvement, strategic oversight |
| **CEO** | Full authority, replaces CEO agent | Human IS the CEO, agents are the team |
| **Manager** | Department-level authority | Manages one team/department directly |
| **Observer** | Read-only | Watch the company operate, no intervention |
| **Pair Programmer** | Direct collaboration with one agent | Work alongside a specific agent in real-time |

---

## 14. Templates & Builder

### 14.1 Template System

Templates are YAML/JSON files defining a complete company setup:

```yaml
# templates/startup.yaml
template:
  name: "Tech Startup"
  description: "Small team for building MVPs and prototypes"
  version: "1.0"

  company:
    name: "{{ company_name }}"
    type: "startup"
    budget_monthly: "{{ budget | default(50.00) }}"
    autonomy: "semi"

  agents:
    - role: "ceo"
      name: "{{ ceo_name | auto }}"
      model: "large"
      personality_preset: "visionary_leader"

    - role: "full_stack_developer"
      name: "{{ dev1_name | auto }}"
      level: "senior"
      model: "medium"
      personality_preset: "pragmatic_builder"

    - role: "full_stack_developer"
      name: "{{ dev2_name | auto }}"
      level: "mid"
      model: "small"
      personality_preset: "eager_learner"

    - role: "product_manager"
      name: "{{ pm_name | auto }}"
      model: "medium"
      personality_preset: "user_advocate"

  workflow: "agile_kanban"
  communication: "hybrid"
```

### 14.2 Company Builder

Interactive CLI/web wizard for creating custom companies:

```bash
$ ai-company create

? Company name: Acme Corp
? Template: [Custom]
? Budget (monthly USD): 100
? Autonomy level: semi-autonomous

? Add departments:
  [x] Engineering
  [x] Product
  [ ] Design
  [ ] Marketing
  [ ] Operations

? Engineering team size: 5
  - 1x Lead (large)
  - 2x Senior Dev (medium)
  - 2x Junior Dev (small)

? Add QA? yes
  - 1x QA Lead (medium)
  - 1x QA Engineer (small)

? Model providers:
  [x] Cloud API
  [x] Local Ollama
  [ ] OpenRouter

Created company "Acme Corp" with 9 agents.
Run: ai-company start acme-corp
```

### 14.3 Community Marketplace (Future)

- Share company templates
- Share custom role definitions
- Share workflow configurations
- Rating and review system
- Import/export in standard format

---

## 15. Technical Architecture

### 15.1 High-Level Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                        AI Company Engine                      │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Company Mgr  │  │ Agent Engine  │  │ Task/Workflow Eng. │  │
│  │ (Config,     │  │ (Lifecycle,   │  │ (Queue, Routing,   │  │
│  │  Templates,  │  │  Personality, │  │  Dependencies,     │  │
│  │  Hierarchy)  │  │  Execution)   │  │  Scheduling)       │  │
│  └──────────────┘  └──────────────┘  └────────────────────┘  │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Comms Layer  │  │ Memory Layer  │  │ Tool/Capability    │  │
│  │ (Message Bus,│  │ (Pluggable,  │  │ System (MCP,       │  │
│  │  Meetings,   │  │  Retrieval,  │  │  Sandboxing,       │  │
│  │  A2A)        │  │  Archive)    │  │  Permissions)      │  │
│  └──────────────┘  └──────────────┘  └────────────────────┘  │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Provider Lyr │  │ Budget/Cost  │  │ Security/Approval  │  │
│  │ (Unified,   │  │ Engine       │  │ System             │  │
│  │  Routing,    │  │ (Tracking,   │  │ (SecOps Agent,     │  │
│  │  Fallbacks)  │  │  Limits,     │  │  Audit Log,        │  │
│  │              │  │  CFO Agent)  │  │  Human Queue)      │  │
│  └──────────────┘  └──────────────┘  └────────────────────┘  │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              API Layer (Async Framework + WebSocket)      │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌──────────────────────┐  ┌─────────────────────────────┐  │
│  │     Web UI (Local)    │  │         CLI Tool            │  │
│  │     Web Dashboard      │  │    ai-company <command>     │  │
│  └──────────────────────┘  └─────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 15.2 Technology Stack (Candidates - TBD After Research)

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Language** | Python 3.14+ | Best AI/ML ecosystem, all major frameworks use it, LiteLLM/MCP and memory layer candidates all Python-native. PEP 649 native lazy annotations, PEP 758 except syntax. |
| **API Framework** | FastAPI | Async-native, WebSocket support, auto OpenAPI docs, high performance, type-safe with Pydantic |
| **LLM Abstraction** | LiteLLM | 100+ providers, unified API, built-in cost tracking, retries/fallbacks |
| **Agent Memory** | TBD (candidates: Mem0, Zep, Letta, Cognee, custom) + SQLite | Memory layer library TBD after evaluation. SQLite for structured data. Upgrade to Postgres later |
| **Message Bus** | Internal (async queues) → Redis | Start with Python asyncio queues, upgrade to Redis for multi-process/distributed |
| **Task Queue** | Internal → Celery/Redis | Start simple, scale with Celery when needed |
| **Database** | SQLite → PostgreSQL | Start lightweight, migrate to Postgres for production/multi-user |
| **Web UI** | Vue 3 + Vite | Modern, fast, good ecosystem. Simpler than React for dashboards |
| **Real-time** | WebSocket (FastAPI native) | Real-time agent activity, task updates, chat feed |
| **Containerization** | Docker + Docker Compose | Isolated code execution, reproducible environments |
| **Tool Integration** | MCP (Model Context Protocol) | Industry standard for LLM-to-tool integration |
| **Agent Comms** | A2A Protocol compatible | Future-proof inter-agent communication |
| **Config Format** | YAML + Pydantic validation | Human-readable config with strict validation |
| **CLI** | Typer (Click-based) | Pythonic CLI framework, auto-help, completions |

### 15.3 Project Structure

Files marked with a milestone tag (e.g. `(M3)`) are planned but do not exist yet — only stub `__init__.py` files are present. All other files listed below exist in the codebase.

```text
ai-company/
├── src/
│   └── ai_company/
│       ├── __init__.py
│       ├── constants.py             # Top-level constants
│       ├── py.typed                 # PEP 561 type marker
│       ├── config/                  # Configuration loading & validation
│       │   ├── schema.py           # Pydantic models for all config
│       │   ├── loader.py           # YAML/JSON config loader
│       │   ├── defaults.py         # Default configurations
│       │   ├── errors.py           # Config error classes
│       │   └── utils.py            # Config utilities
│       ├── core/                    # Core domain models
│       │   ├── agent.py            # AgentIdentity (frozen)
│       │   ├── types.py            # Shared validated types (NotBlankStr, etc.)
│       │   ├── company.py          # Company structure
│       │   ├── enums.py            # Core enumerations
│       │   ├── task.py             # Task model & state machine
│       │   ├── task_transitions.py # Task state transitions
│       │   ├── project.py          # Project management
│       │   ├── artifact.py         # Produced work items
│       │   ├── role.py             # Role model
│       │   └── role_catalog.py     # Role catalog
│       ├── engine/                  # Agent orchestration, execution loops, and task lifecycle
│       │   ├── errors.py           # Engine error hierarchy
│       │   ├── prompt.py           # System prompt builder
│       │   ├── prompt_template.py  # System prompt Jinja2 templates
│       │   ├── task_execution.py   # TaskExecution + StatusTransition
│       │   ├── context.py          # AgentContext + AgentContextSnapshot
│       │   ├── loop_protocol.py    # ExecutionLoop protocol + result models
│       │   ├── metrics.py          # TaskCompletionMetrics proxy overhead model
│       │   ├── react_loop.py       # ReAct loop implementation
│       │   ├── run_result.py       # AgentRunResult outcome model
│       │   ├── agent_engine.py     # Agent execution engine
│       │   ├── task_engine.py      # Task routing & scheduling (M3-M4)
│       │   ├── workflow_engine.py  # Workflow orchestration (M4)
│       │   ├── meeting_engine.py   # Meeting coordination (M4)
│       │   └── hr_engine.py        # Hiring, firing, performance (M7)
│       ├── communication/           # Inter-agent communication
│       │   ├── channel.py          # Channel model
│       │   ├── message.py          # Message model
│       │   ├── config.py           # Communication config
│       │   └── enums.py            # Communication enums
│       ├── memory/                  # Agent memory system (M5, stubs only)
│       │   ├── store.py            # Memory storage backend (M5)
│       │   ├── retrieval.py        # Memory retrieval & ranking (M5)
│       │   ├── consolidation.py    # Memory compression over time (M5)
│       │   └── shared.py           # Shared knowledge base (M5)
│       ├── observability/           # Structured logging & correlation
│       │   ├── __init__.py         # get_logger() entry point
│       │   ├── _logger.py          # Logger configuration
│       │   ├── config.py           # Observability config
│       │   ├── correlation.py      # Correlation ID tracking
│       │   ├── enums.py            # Log-related enums
│       │   ├── events/             # Per-domain event constants
│       │   │   ├── __init__.py    # Package marker with usage docs; no re-exports
│       │   │   ├── budget.py      # BUDGET_* constants
│       │   │   ├── config.py      # CONFIG_* constants
│       │   │   ├── execution.py   # EXECUTION_* constants
│       │   │   ├── prompt.py      # PROMPT_* constants
│       │   │   ├── provider.py    # PROVIDER_* constants
│       │   │   ├── role.py        # ROLE_* constants
│       │   │   ├── routing.py     # ROUTING_* constants
│       │   │   ├── task.py        # TASK_* constants
│       │   │   ├── template.py    # TEMPLATE_* constants
│       │   │   └── tool.py        # TOOL_* constants
│       │   ├── processors.py       # Log processors
│       │   ├── setup.py            # Logging setup
│       │   └── sinks.py            # Log output backends
│       ├── providers/               # LLM provider abstraction
│       │   ├── base.py             # BaseCompletionProvider (retry + rate limiting)
│       │   ├── protocol.py         # Provider protocol (abstract interface)
│       │   ├── models.py           # CompletionConfig/Response, TokenUsage, ToolCall/Result
│       │   ├── capabilities.py     # Provider capability registry
│       │   ├── registry.py         # Provider registry
│       │   ├── enums.py            # Provider enumerations
│       │   ├── errors.py           # Provider error hierarchy
│       │   ├── drivers/            # Provider driver implementations
│       │   │   ├── litellm_driver.py  # LiteLLM adapter
│       │   │   └── mappers.py     # Request/response mappers
│       │   ├── routing/            # Model routing (6 strategies)
│       │   │   ├── _strategy_helpers.py  # Shared routing helper functions
│       │   │   ├── errors.py      # Routing errors
│       │   │   ├── models.py      # Routing models (candidates, results)
│       │   │   ├── resolver.py    # Model resolver
│       │   │   ├── router.py      # Router orchestrator
│       │   │   └── strategies.py  # Routing strategies
│       │   └── resilience/         # Resilience patterns
│       │       ├── config.py      # RetryConfig, RateLimiterConfig
│       │       ├── errors.py      # RetryExhaustedError
│       │       ├── rate_limiter.py # Token bucket rate limiter
│       │       └── retry.py       # RetryHandler with backoff
│       ├── tools/                   # Tool/capability system
│       │   ├── base.py             # BaseTool ABC, ToolExecutionResult
│       │   ├── registry.py         # Immutable tool registry (MappingProxyType)
│       │   ├── invoker.py          # Tool invocation (concurrent via TaskGroup)
│       │   ├── errors.py           # Tool error hierarchy
│       │   ├── examples/           # Example tool implementations
│       │   │   └── echo.py        # Echo tool (for testing)
│       │   ├── sandbox/            # Sandboxing backends (M3)
│       │   │   ├── protocol.py    # SandboxBackend protocol
│       │   │   ├── subprocess.py  # SubprocessSandbox (default for low-risk)
│       │   │   └── docker.py      # DockerSandbox (for code_runner, terminal)
│       │   ├── file_system.py      # File operations (M3)
│       │   ├── git_tools.py        # Git operations (M3)
│       │   ├── code_runner.py      # Code execution (M3)
│       │   ├── web_tools.py        # HTTP, search (M3)
│       │   └── mcp_bridge.py       # MCP server integration (M7)
│       ├── security/                # Security & approval (M7, stubs only)
│       │   ├── approval.py         # Approval workflow (M7)
│       │   ├── secops_agent.py     # Security operations agent (M7)
│       │   ├── audit.py            # Audit logging (M7)
│       │   └── permissions.py      # Permission checking (M7)
│       ├── budget/                  # Cost management
│       │   ├── config.py           # Budget configuration models
│       │   ├── cost_record.py      # CostRecord model (frozen)
│       │   ├── tracker.py          # CostTracker service (records + queries)
│       │   ├── spending_summary.py # _SpendingTotals base + spending summary models
│       │   ├── hierarchy.py        # BudgetHierarchy, BudgetConfig
│       │   ├── enums.py            # Budget-related enums
│       │   ├── limits.py           # Budget enforcement (M5)
│       │   ├── optimizer.py        # Cost optimization / CFO logic (M5)
│       │   └── reports.py          # Spending reports (M5)
│       ├── api/                     # REST + WebSocket API (M6, stubs only)
│       │   ├── app.py              # FastAPI application (M6)
│       │   ├── routes/             # Route handlers (M6)
│       │   ├── websocket.py        # WebSocket handlers (M6)
│       │   └── middleware.py       # Auth, CORS, logging (M6)
│       ├── cli/                     # CLI interface (M6, stubs only)
│       │   ├── main.py             # Typer app (M6)
│       │   ├── commands/           # CLI commands (M6)
│       │   └── display.py          # Rich terminal output (M6)
│       └── templates/               # Company templates
│           ├── schema.py           # Template schema models
│           ├── loader.py           # Template loader
│           ├── renderer.py         # Template renderer
│           ├── presets.py          # Template presets
│           ├── errors.py           # Template errors
│           └── builtins/           # Pre-built company templates
│               ├── agency.yaml
│               ├── dev_shop.yaml
│               ├── full_company.yaml
│               ├── product_team.yaml
│               ├── research_lab.yaml
│               ├── solo_founder.yaml
│               └── startup.yaml
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/
│   └── getting_started.md
├── DESIGN_SPEC.md                   # This document
├── README.md
├── pyproject.toml
└── CLAUDE.md
```

### 15.4 Key Design Decisions (Preliminary - Subject to Research)

| Decision | Choice | Alternatives Considered | Rationale |
|----------|--------|------------------------|-----------|
| Language | Python 3.14+ | TypeScript, Go, Rust | AI ecosystem, LiteLLM/MCP and memory layer candidates are Python-native, PEP 649 lazy annotations, PEP 758 except syntax |
| API | FastAPI | Flask, Django, aiohttp | Async native, Pydantic integration, auto docs, WebSocket support |
| LLM Layer | LiteLLM | Direct APIs, OpenRouter only | 100+ providers, cost tracking, fallbacks, load balancing built-in |
| Memory | TBD + SQLite | Mem0, Zep, Letta, Cognee, ChromaDB, custom | Memory layer library TBD — all candidates under evaluation. Must support episodic, semantic, procedural memory types (§7.1–7.3). Org memory served via `OrgMemoryBackend` protocol (§7.4) |
| Message Bus | asyncio queues → Redis | Kafka, RabbitMQ, NATS | Start simple, Redis well-supported, Kafka overkill for local |
| Config | YAML + Pydantic | JSON, TOML, Python dicts | Human-friendly, strict validation, good IDE support |
| CLI | Typer | Click, argparse, Fire | Built on Click, auto-completion, type hints |
| Web UI | Vue 3 | React, Svelte, HTMX | Simpler than React for dashboards, good with FastAPI |
| Sandboxing | Layered: subprocess + Docker | Docker-only, subprocess-only, WASM | Risk-proportionate: fast subprocess for file/git, Docker isolation for code execution. Pluggable `SandboxBackend` protocol enables K8s migration later |

### 15.5 Engineering Conventions

These conventions were established during the M0–M2+ review cycle. **Adopted** conventions are already used throughout the codebase. **Planned** conventions are approved design decisions for upcoming milestones but not yet implemented.

| Convention | Status | Decision | Rationale |
|------------|--------|----------|-----------|
| **Immutability strategy** | Adopted | `copy.deepcopy()` at construction + `MappingProxyType` wrapping for non-Pydantic internal collections (registries, `BaseTool`). For Pydantic frozen models: `frozen=True` prevents field reassignment; `copy.deepcopy()` at system boundaries (tool execution, LLM provider serialization) prevents nested mutation. No MappingProxyType inside Pydantic models (serialization friction). | Deep-copy at construction fully isolates nested structures; `MappingProxyType` enforces read-only access. Boundary-copy for Pydantic models is simple, centralized, and Pydantic-native. A future CPython built-in immutable mapping type (e.g. `frozendict`) would provide zero-friction field-level immutability when available. |
| **Config vs runtime split** | Adopted (M3) | Frozen models for config/identity; `model_copy(update=...)` for runtime state transitions | `TaskExecution` and `AgentContext` (in `engine/`) are frozen Pydantic models that use `model_copy(update=...)` for copy-on-write state transitions without re-running validators (per Pydantic `model_copy` semantics). Config layer (`AgentIdentity`, `Task`) remains unchanged. |
| **Derived fields** | Adopted | `@computed_field` instead of stored + validated | Eliminates redundant storage and impossible-to-fail validators. `TokenUsage.total_tokens` migrated from stored `Field` + `@model_validator` to `@computed_field` property. |
| **String validation** | Adopted | `NotBlankStr` type from `core.types` for all identifiers | Eliminates per-model `@model_validator` boilerplate for whitespace checks. All identifier/name fields use `NotBlankStr`; optional identifiers use `NotBlankStr \| None`; tuple fields use `tuple[NotBlankStr, ...]` for per-element validation. |
| **Shared field groups** | Adopted (M2.5) | Extracted common field sets into base models (e.g. `_SpendingTotals`) | Prevents field duplication across spending summary models. `_SpendingTotals` provides shared aggregation fields; `AgentSpending`, `DepartmentSpending`, `PeriodSpending` extend it. |
| **Event constants** | Adopted (per-domain) | Per-domain submodules under `events/` package (e.g. `events.provider`, `events.budget`). Import directly: `from ai_company.observability.events.<domain> import CONSTANT` | Split by domain for discoverability, co-location with domain logic, and reduced merge conflicts as constants grow. `__init__.py` serves as package marker with usage documentation; no re-exports. |
| **Parallel tool execution** | Adopted (M2.5) | `asyncio.TaskGroup` in `ToolInvoker.invoke_all` with optional `max_concurrency` semaphore | Structured concurrency with proper cancellation semantics. Fatal errors collected via guarded wrapper and re-raised after all tasks complete. |
| **Tool sandboxing** | Planned (M3) | Layered `SandboxBackend` protocol: `SubprocessSandbox` for low-risk tools (file, git), `DockerSandbox` for high-risk tools (code_runner, terminal, web, database). `K8sSandbox` planned for future container deployments. | Risk-proportionate isolation. Docker optional — only needed for code execution and network-sensitive tools. Pluggable protocol enables seamless migration to K8s per-agent pods in Phase 3-4. See §11.1.2. |
| **Crash recovery** | Planned (M3) | Pluggable `RecoveryStrategy` protocol. M3: `FailAndReassignStrategy` (catch at engine boundary, log snapshot, mark FAILED, reassign). M4/M5: `CheckpointStrategy` (persist `AgentContext` per turn, resume from last checkpoint). | Immutable `model_copy` pattern makes checkpoint serialization trivial to add later. Fail-and-reassign is sufficient for short MVP tasks. See §6.6. |
| **Agent behavior testing** | Planned (M3) | Scripted `FakeProvider` for unit tests (deterministic turn sequences); behavioral outcome assertions for integration tests (task completed, tools called, cost within budget). | Leverages existing `FakeProvider` and `CompletionResponseFactory` fixtures. Precise engine testing without brittle response-matching at integration level. |
| **LLM call analytics** | Planned (incremental) | M3: proxy metrics (`turns_per_task`, `tokens_per_task`). M4: call categorization (`productive`, `coordination`, `system`) + orchestration ratio. M5+: full analytics (retry tracking, latency, cache hits, per-provider comparison). | Append-only, never blocks execution. Builds on existing `CostRecord` infrastructure. Detects orchestration overhead early. See §10.5. |
| **State coordination** | Planned (M4) | Centralized single-writer: `TaskEngine` owns all task/project mutations via `asyncio.Queue`. Agents submit requests, engine applies `model_copy(update=...)` sequentially and publishes snapshots. `version: int` field on state models for future optimistic concurrency if multi-process scaling is needed. | Prevents lost updates by design. Trivial in single-threaded asyncio (no locks). Perfect audit trail. Industry consensus: MetaGPT, CrewAI, AutoGen all use prevention-by-design, not conflict resolution. See §6.8 State Coordination table. |
| **Workspace isolation** | Planned (M4) | Pluggable `WorkspaceIsolationStrategy` protocol. Default: planner + git worktrees. Each agent works in an isolated worktree; sequential merge on completion. Textual conflicts detected by git; semantic conflicts reviewed by agent or human. | Industry standard (Codex, Cursor, Claude Code, VS Code). Maximum parallelism. Leverages mature git infrastructure. See §6.8. |
| **Graceful shutdown** | Planned (M3) | Pluggable `ShutdownStrategy` protocol. Default: cooperative with 30s timeout. Agents check shutdown event at turn boundaries. Force-cancel after timeout. `INTERRUPTED` status for force-cancelled tasks. M4/M5: upgrade to checkpoint-and-stop. | Cross-platform (Windows `signal.signal()` fallback). Bounded shutdown time. Mirrors cooperative shutdown in §6.7. |

---

## 16. Research & Prior Art

### 16.1 Existing Frameworks Comparison

| Framework | Stars | Architecture | Roles | Models | Memory | Custom Roles | Production Ready |
|-----------|-------|-------------|-------|--------|--------|-------------|-----------------|
| **MetaGPT** | 64.5k | SOP-driven pipeline | PM, Architect, Engineer, QA | OpenAI, Ollama, Groq, Azure | Limited | Partial | Research → MGX commercial |
| **ChatDev 2.0** | 31.2k | Zero-code visual workflows | CEO, CTO, Programmer, Tester, Designer | Multiple via config | Limited | Yes (YAML) | Improving (v2.0 Jan 2026) |
| **CrewAI** | ~50k+ | Role-based crews + flows | Fully custom | Multi-provider | Basic (crew memory) | Yes | Yes (100k+ developers) |
| **AutoGen** | ~40k+ | Conversation-driven async | Custom agents | OpenAI primary, others | Session-based | Yes | Transitioning to MS Agent Framework |
| **LangGraph** | Large | Graph-based DAG | Custom nodes | LangChain ecosystem | Stateful graphs | Yes (nodes) | Yes |
| **Smolagents** | Growing | Code-centric minimal | Code agent | HuggingFace ecosystem | Minimal | Yes | Rapid prototyping |

### 16.2 What Exists vs What We Need

| Feature | MetaGPT | ChatDev | CrewAI | **AI Company (Ours)** |
|---------|---------|---------|--------|----------------------|
| Full company simulation | Partial | Partial | No | **Yes - complete** |
| HR (hiring/firing) | No | No | No | **Yes** |
| Budget management (CFO) | No | No | No | **Yes** |
| Persistent agent memory | No | No | Basic | **Yes (memory layer TBD — candidates under evaluation)** |
| Agent personalities | Basic | Basic | Basic | **Deep - traits, styles, evolution** |
| Dynamic team scaling | No | No | Manual | **Yes - auto + manual** |
| Multiple company types | No | No | Manual | **Yes - templates + builder** |
| Security ops agent | No | No | No | **Yes** |
| Configurable autonomy | No | No | Limited | **Yes - full spectrum** |
| Local + cloud providers | Partial | Partial | Partial | **Yes - unified abstraction (LiteLLM candidate)** |
| Cost tracking per agent | No | No | No | **Yes - full budget system** |
| Progressive trust | No | No | No | **Yes** |
| Performance metrics | No | No | No | **Yes** |
| MCP tool integration | No | No | Partial | **Yes** |
| A2A protocol support | No | No | No | **Planned** |
| Community marketplace | MGX (commercial) | No | No | **Planned (backlog)** |

### 16.3 Agent Scaling Research

[Kim et al., "Towards a Science of Scaling Agent Systems" (2025)](https://arxiv.org/abs/2512.08296) — 180 controlled experiments across 3 LLM families (OpenAI, Google, Anthropic), 4 agentic benchmarks, 5 coordination topologies. Key findings informing our design:

- **Task decomposability is the #1 predictor** of multi-agent success. Parallelizable tasks gain up to +81%, sequential tasks degrade -39% to -70% under all MAS variants. Informs §6.9.
- **Coordination metrics suite** (efficiency, overhead, error amplification, message density, redundancy) explains 52.4% of performance variance (R²=0.524). Adopted in §10.5.
- **Tiered coordination overhead** (`O%`): optimal band 200–300%, over-coordination above 400%. Informs §10.5 interpretation of the `O%` metric. Note: the `orchestration_ratio` tiered alerts (info/warn/critical) measure a different ratio (coordination tokens / total tokens).
- **Error taxonomy** (logical contradiction, numerical drift, context omission, coordination failure) with architecture-specific patterns. Adopted as opt-in classification in §10.5.
- **Auto topology selection** achieves 87% accuracy from measurable task properties. Informs §6.9 auto topology selector.
- **Centralized verification** contains error amplification to 4.4× vs 17.2× for independent agents. Supports §6.9's centralized-topology guidance and §10.5's `Ae` metric interpretation.
- **Context:** Paper tested identical agents on individual tasks; our architecture uses role-differentiated agents in an organizational structure. Thresholds (e.g., 45% capability ceiling, 3–4 agent sweet spot) are directional — to be validated empirically in our context.

### 16.4 Build vs Fork Decision

**Recommendation: Build from scratch, leverage libraries.**

Rationale:
- No existing framework covers even 50% of our requirements
- Our core differentiators (HR, budget, security ops, deep personalities, progressive trust) don't exist in any framework
- Forking MetaGPT or CrewAI would mean fighting their architecture while adding our features
- **LiteLLM**, **FastAPI**, **MCP**, and a memory layer library (TBD) give us battle-tested components for the hard parts
- The "company simulation" layer on top is our unique value and must be purpose-built

What we **plan to leverage** (not fork) — subject to evaluation:
- **LiteLLM** (candidate) - Provider abstraction
- **Memory layer** (candidates: Mem0, Zep, Letta, Cognee, custom) - Agent memory
- **FastAPI** (candidate) - API layer
- **MCP** - Tool integration standard (strong candidate, emerging industry standard)
- **Pydantic** (candidate) - Config validation and data models
- **Typer** (candidate) - CLI
- **Web UI framework** - TBD (Vue 3, React, Svelte, HTMX all under consideration)

---

## 17. Open Questions & Risks

### 17.1 Open Questions

| # | Question | Impact | Status | Notes |
|---|----------|--------|--------|-------|
| 1 | How deep should agent personality affect output? | Medium | Open | Too deep = inconsistent, too shallow = all agents feel the same |
| 2 | What is the optimal meeting format for multi-agent? | High | **Resolved** | Multiple configurable protocols — see §5.7 Meeting Protocol |
| 3 | How to handle context window limits for long tasks? | High | Open | Agents may lose track of complex multi-file changes |
| 4 | Should agents be able to create/modify other agents? | Medium | Open | CTO "hires" a dev by creating a new agent config |
| 5 | How to handle conflicting agent opinions? | High | **Resolved** | Multiple configurable strategies — see §5.6 Conflict Resolution Protocol |
| 6 | What metrics define "good" agent performance? | Medium | Open | Needed for HR/hiring/firing decisions |
| 7 | How to prevent agent communication loops? | High | **Resolved** | Implemented in §5.5 Loop Prevention |
| 8 | Optimal message bus for local-first architecture? | Medium | Open | asyncio queues vs Redis vs embedded broker |
| 9 | How to handle code execution safely? | High | **Resolved** | Layered sandboxing behind `SandboxBackend` protocol — see §11.1.2 Tool Sandboxing |
| 10 | What's the minimum viable meeting set? | Low | Open | Standup + planning + review as minimum? |
| 11 | What is the agent execution loop architecture? | High | **Resolved** | Multiple configurable loops — see §6.5 Agent Execution Loop |
| 12 | How should shared organizational memory work? | High | **Resolved** | Modular backends behind protocol — see §7.4 Shared Organizational Memory |
| 13 | What happens when humans don't respond to approvals? | High | **Resolved** | Configurable timeout policies with task suspension — see §12.4 Approval Timeout |
| 14 | Which memory layer library to use? | Medium | Open | Mem0, Zep, Letta, Cognee, custom — all candidates, TBD after evaluation (see §15.2) |
| 15 | How to handle agent crashes mid-task? | High | **Resolved** | Pluggable `RecoveryStrategy` protocol — see §6.6 Agent Crash Recovery |
| 16 | How to test non-deterministic agent behavior? | High | **Resolved** | Scripted providers for unit tests + behavioral assertions for integration — see §15.5 Engineering Conventions |
| 17 | How to detect orchestration overhead? | Medium | **Resolved** | Incremental LLM call analytics with proxy metrics (M3) → full categorization (M4) — see §10.5 |

### 17.2 Technical Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Context window exhaustion on complex tasks | High | Memory summarization, task decomposition, working memory management |
| Cost explosion from agent loops | High | Budget hard stops, loop detection, max iterations per task |
| Agent quality degradation with cheap models | Medium | Quality gates, minimum model requirements per task type |
| Third-party library breaking changes | Medium | Pin versions, integration tests, abstraction layers |
| Memory retrieval quality | Medium | Evaluate candidates (Mem0, custom, etc.) against our use case |
| Agent personality inconsistency | Low | Strong system prompts, few-shot examples, personality tests |
| WebSocket scaling | Low | Start local, add Redis pub/sub when needed |

### 17.3 Architecture Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Over-engineering the MVP | High | Start with minimal viable company (3-5 agents), add complexity iteratively |
| Config format becoming unwieldy | Medium | Good defaults, layered config (base + overrides), validation |
| Agent execution bottlenecks | Medium | Async execution, parallel agent processing, queue-based |
| Data loss on crash | Medium | WAL mode SQLite, `RecoveryStrategy` protocol (§6.6): fail-and-reassign in MVP, checkpoint recovery in M4/M5 |
| Orchestration overhead exceeds productive work | Medium | LLM call analytics (§10.5): proxy metrics from M3, call categorization + orchestration ratio alerts from M4 |

---

## 18. Backlog & Future Vision

### 18.1 Future Features (Not for MVP)

| Feature | Priority | Description |
|---------|----------|-------------|
| Community marketplace | Medium | Share/download company templates, roles, workflows |
| Network hosting | Medium | Expose on LAN/internet, multi-user access |
| Agent evolution | Medium | Agents improve over time based on feedback |
| Inter-company communication | Low | Two AI companies collaborating on a project |
| Voice interface | Low | Talk to your AI company via voice |
| Mobile app | Low | Monitor your company from phone |
| Plugin system | High | Third-party plugins for new tools, roles, providers |
| Benchmarking suite | Medium | Compare company configurations on standard tasks |
| Visual workflow editor | Medium | Drag-and-drop workflow design in Web UI |
| Multi-project support | High | Company handles multiple projects simultaneously |
| Client simulation | Low | AI "clients" that give requirements and review output |
| Training mode | Medium | New agents learn from senior agents' past work |
| ~~Conflict resolution protocol~~ | ~~High~~ | ~~Moved to core — see §5.6~~ |
| Agent promotions | Medium | Junior → Mid → Senior based on performance |
| Shift system | Low | Agents "work" in shifts, different agents for different hours |
| Reporting system | Medium | Weekly/monthly automated company reports |
| Integration APIs | Medium | Connect to real Slack, GitHub, Jira, Linear |
| Self-improving company | High | The AI company developing AI company (meta!) |

### 18.2 Scaling Path

```text
Phase 1: Local Single-Process
  └── Async runtime, embedded DB, in-memory bus, 1-10 agents

Phase 2: Local Multi-Process
  └── External message bus, production DB, sandboxed execution, 10-30 agents

Phase 3: Network/Server
  └── Full API, multi-user, distributed agents, 30-100 agents

Phase 4: Cloud/Hosted
  └── Container orchestration, horizontal scaling, marketplace, 100+ agents
```

---

## Appendix A: Industry Standards Reference

| Standard | Owner | Purpose | Our Usage |
|----------|-------|---------|-----------|
| **MCP** (Model Context Protocol) | Anthropic → Linux Foundation (AAIF) | LLM ↔ Tool integration | Tool system backbone |
| **A2A** (Agent-to-Agent Protocol) | Google → Linux Foundation | Agent ↔ Agent communication | Future agent interop |
| **OpenAI API format** | OpenAI (de facto standard) | LLM API interface | Via provider abstraction layer (LiteLLM candidate) |

## Appendix B: Research Sources

- [MetaGPT](https://github.com/FoundationAgents/MetaGPT) - Multi-agent SOP framework (64.5k stars)
- [ChatDev 2.0](https://github.com/openbmb/ChatDev) - Zero-code multi-agent platform (31.2k stars)
- [CrewAI](https://github.com/crewAIInc/crewAI) - Role-based agent collaboration framework
- [AutoGen](https://github.com/microsoft/autogen) - Microsoft async multi-agent framework
- [LiteLLM](https://github.com/BerriAI/litellm) - Unified LLM API gateway (100+ providers)
- [Mem0](https://github.com/mem0ai/mem0) - Universal memory layer for AI agents
- [A2A Protocol](https://github.com/a2aproject/A2A) - Agent-to-Agent protocol (Linux Foundation)
- [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25) - Model Context Protocol
- [Langfuse Agent Comparison](https://langfuse.com/blog/2025-03-19-ai-agent-comparison) - Framework comparison
- [Confluent Event-Driven Patterns](https://www.confluent.io/blog/event-driven-multi-agent-systems/) - Multi-agent architecture patterns
- [Microsoft Multi-Agent Reference Architecture](https://microsoft.github.io/multi-agent-reference-architecture/) - Enterprise patterns
- [OpenRouter](https://openrouter.ai/) - Multi-model API gateway
- [Kim et al., "Towards a Science of Scaling Agent Systems" (2025)](https://arxiv.org/abs/2512.08296) - Empirical agent scaling research (180 experiments, 3 LLM families)
- [Cemri et al., "Multi-Agent System Failure Taxonomy (MAST)" (2025)] - MAS coordination error classification
