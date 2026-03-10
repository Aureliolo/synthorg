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
7. [Memory & Persistence](#7-memory--persistence) — 7.4 Shared Org Memory (Research Directions), **7.5 Memory Backend Protocol**, **7.6 Operational Data Persistence**
8. [HR & Workforce Management](#8-hr--workforce-management)
9. [Model Provider Layer](#9-model-provider-layer)
10. [Cost & Budget Management](#10-cost--budget-management)
11. [Tool & Capability System](#11-tool--capability-system) — **11.1.3 MCP Integration**, **11.1.4 Action Type System**, 11.3 Progressive Trust
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

> **Implementation snapshot (2026-03-10):**
> - **Done:** M0–M6 (tooling, config/core, providers, single-agent engine, multi-agent orchestration, API/CLI surface) + Docker sandbox (#50), MCP bridge (#53), code runner + HR engine (hiring/firing/onboarding/offboarding/registry) + performance tracking (task metrics, quality scoring, collaboration scoring, trend detection, rolling windows). Memory layer backend selected ([ADR-001](docs/decisions/ADR-001-memory-layer.md)). Persistence backend (§7.6) completed (including audit entry persistence via AuditRepository + SQLite backend). Memory retrieval pipeline (#41: ranking, token-budget formatting, context injection, non-inferable filtering) complete. Budget enforcement complete (BudgetEnforcer + configurable cost tiers + quota/subscription tracking). CFO cost optimization complete (CostOptimizer: anomaly detection, efficiency analysis, downgrade recommendations, routing optimization, approval decisions; ReportGenerator: multi-dimensional spending reports). Shared org memory (#125: HybridPromptRetrievalBackend, OrgFactStore, access control, factory) complete. Memory consolidation/archival (#48: ConsolidationService, SimpleConsolidationStrategy, RetentionEnforcer, ArchivalStore protocol) complete. SecOps agent (rule engine, audit log, output scanner, output scan response policies (redact/withhold/log-only/autonomy-tiered), risk classifier, ToolInvoker integration), progressive trust (4 strategies: disabled/weighted/per-category/milestone behind TrustStrategy protocol), promotion/demotion (criteria evaluation, approval strategies, model mapping). Autonomy levels (#42: AutonomyLevel enum, presets, 3-level resolver, rule-based auto-downgrade/human-only promotion change strategy) + approval timeout policies (#126: 4 timeout policies, park/resume service, risk tier classifier, timeout checker) complete.
> - **Remaining:** JWT/OAuth auth, approval workflow gates.

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

**Personality dimensions** split into two tiers:

- **Big Five (OCEAN-variant)** — floats (0.0–1.0) used for internal compatibility scoring only (not injected into prompts). `stress_response` replaces traditional neuroticism with inverted polarity (1.0 = very calm). Scored by `core/personality.py`.
- **Behavioral enums** — injected into system prompts as natural-language labels that LLMs respond to:
  - `DecisionMakingStyle`: `analytical`, `intuitive`, `consultative`, `directive`
  - `CollaborationPreference`: `independent`, `pair`, `team`
  - `CommunicationVerbosity`: `terse`, `balanced`, `verbose`
  - `ConflictApproach`: `avoid`, `accommodate`, `compete`, `compromise`, `collaborate` (Thomas-Kilmann model)

```yaml
# --- Current (M3): Config layer — AgentIdentity (frozen) ---
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
    # Big Five (OCEAN-variant) dimensions — internal scoring (0.0-1.0)
    openness: 0.4              # curiosity, creativity
    conscientiousness: 0.9     # thoroughness, reliability
    extraversion: 0.3          # assertiveness, sociability
    agreeableness: 0.5         # cooperation, empathy
    stress_response: 0.75      # emotional stability (1.0 = very calm)
    # Behavioral enums — injected into system prompts
    decision_making: "analytical"    # analytical, intuitive, consultative, directive
    collaboration: "independent"     # independent, pair, team
    verbosity: "balanced"            # terse, balanced, verbose
    conflict_approach: "compromise"  # avoid, accommodate, compete, compromise, collaborate
  skills:
    primary:
      - python
      - litestar
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
    access_level: "standard"  # sandboxed | restricted | standard | elevated | custom
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
  autonomy_level: null           # optional: full, semi, supervised, locked (overrides department/company default, §12.2)
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

> **Current state (M4 complete):** The communication foundation is implemented: `MessageBus` protocol with `InMemoryMessageBus` backend (asyncio queues, pull-model `receive()`), `MessageDispatcher` for concurrent handler routing via `asyncio.TaskGroup`, `AgentMessenger` per-agent facade (auto-fills sender/timestamp/ID, deterministic direct-channel naming `@{sorted_a}:{sorted_b}`), and `DeliveryEnvelope` for delivery tracking. Loop prevention (§5.5) is implemented: `DelegationGuard` orchestrates five mechanisms (ancestry, depth, dedup, rate limit, circuit breaker) with `LoopPreventionConfig`. Hierarchical delegation is implemented via `DelegationService` with `HierarchyResolver` and `AuthorityValidator`. Task model extended with `parent_task_id` and `delegation_chain` fields. Conflict resolution (§5.6) is implemented: `ConflictResolver` protocol with four strategies (Authority, Debate, HumanEscalation, Hybrid), `ConflictResolutionService` orchestrator, `DissentRecord` audit trail, and `HierarchyResolver.get_lowest_common_manager()` for cross-department conflict escalation. Meeting protocol (§5.7) is implemented with all 3 protocols (round-robin, position papers, structured phases) via `MeetingOrchestrator` in `communication/meeting/`.

### 5.6 Conflict Resolution Protocol

When two or more agents disagree on an approach (architecture, implementation, priority, etc.), the framework provides multiple configurable resolution strategies behind a `ConflictResolver` protocol. New strategies can be added without modifying existing ones. The strategy is configurable per company, per department, or per conflict type.

> **MVP: Not in M3.** Conflict resolution is an M4 feature (M3 is single-agent). Authority + Dissent Log (Strategy 1) is the initial default.
>
> **Current state (M4):** All four strategies implemented: `AuthorityResolver` (seniority + hierarchy proximity), `DebateResolver` (judge-based with `JudgeEvaluator` protocol), `HumanEscalationResolver` (stub pending approval queue #37), `HybridResolver` (automated review + escalation). `ConflictResolutionService` orchestrates strategy selection and audit trail (`DissentRecord`). Models: `Conflict`, `ConflictPosition`, `ConflictResolution` (frozen Pydantic). Config: `ConflictResolutionConfig`, `DebateConfig`, `HybridConfig`. `HierarchyResolver` extended with `get_lowest_common_manager()` and `get_delegation_depth()`. Event constants in `observability/events/conflict.py`.

#### Strategy 1: Authority + Dissent Log (Default)

The agent with higher authority level decides. Cross-department conflicts (incomparable authority) escalate to the lowest common manager in the hierarchy. The losing agent's reasoning is preserved as a **dissent record** — a structured log entry containing the conflict context, both positions, and the resolution. Dissent records feed into organizational learning and can be reviewed during retrospectives.

```yaml
conflict_resolution:
  strategy: "authority"            # authority, debate, human, hybrid
```

- Deterministic, zero extra tokens, fast resolution
- Dissent records create institutional memory of alternative approaches

#### Strategy 2: Structured Debate + Judge

Both agents present arguments (1 round each). A judge — their shared manager, the CEO, or a configurable arbitrator agent — evaluates both positions and decides. The judge's reasoning and both arguments are logged as a dissent record.

```yaml
conflict_resolution:
  strategy: "debate"
  debate:
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

1. Both agents present arguments (1 round) — preserving dissent
2. A **conflict review agent** evaluates the result:
   - If the resolution is **clear** (one position is objectively better, or authority applies cleanly) → resolve automatically, log dissent record
   - If the resolution is **ambiguous** (genuine trade-offs, no clear winner) → escalate to human queue with both positions + the review agent's analysis

```yaml
conflict_resolution:
  strategy: "hybrid"
  hybrid:
    review_agent: "conflict_reviewer"  # dedicated agent or role
    escalate_on_ambiguity: true
```

- Best balance: most conflicts resolve fast, humans only see genuinely hard calls
- Most complex to implement; review agent itself needs careful prompt design

### 5.7 Meeting Protocol

Meetings (§5.1 Pattern 3) follow configurable protocols that determine how agents interact during structured multi-agent conversations. Different meeting types naturally suit different protocols. All protocols implement a `MeetingProtocol` protocol, making the system extensible — new protocols can be registered and selected per meeting type. Cost bounds are enforced by `duration_tokens` in meeting config (§5.4).

> **Current state (M4 complete):** All 3 meeting protocols are implemented in `communication/meeting/`: `RoundRobinProtocol`, `PositionPapersProtocol`, and `StructuredPhasesProtocol`. The `MeetingOrchestrator` runs meetings end-to-end with token budget enforcement via `TokenTracker`. Shared LLM response parsing for decisions and action items is in `_parsing.py`. All protocols implement the `MeetingProtocol` protocol interface.

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
auto_create_tasks: true              # action items become tasks (top-level, applies to any protocol)
structured_phases:
  skip_discussion_if_no_conflicts: true
  max_discussion_tokens: 1000
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
                 ┌─────▼─────┐           ┌──────────┐
          ┌──────│ ASSIGNED   │──────────▶│  FAILED   │
          │      └─────┬─────┘◀───┐      └────┬─────┘
          │            │ starts    │ reassign  │
          │      ┌─────▼─────┐    │      ┌────▼─────┐
          │      │IN_PROGRESS │───┼─────▶│  (retry)  │
          │      └─────┬─────┘   │       └──────────┘
          │            │  ◀── (rework)
          │            │ agent done
          │      ┌─────▼─────┐
          │      │ IN_REVIEW  │
          │      └─────┬─────┘
          │            │ approved
          │      ┌─────▼─────┐
          │      │ COMPLETED  │
          │      └────────────┘
          │
          │ blocked          cancelled (from ASSIGNED or IN_PROGRESS)
    ┌─────▼─────┐      ┌────────────┐
    │  BLOCKED   │      │ CANCELLED   │ ◀── ASSIGNED / IN_PROGRESS
    └─────┬─────┘      └────────────┘
          │ unblocked        (terminal)
          └──▶ ASSIGNED

    shutdown signal:
    ┌─────────────┐
    │ INTERRUPTED  │──── reassign on restart ──▶ ASSIGNED
    └─────────────┘
```

> **Non-terminal states:** BLOCKED, FAILED, and INTERRUPTED are non-terminal — BLOCKED returns to ASSIGNED when unblocked, FAILED returns to ASSIGNED for retry (see §6.6), INTERRUPTED returns to ASSIGNED on restart (see §6.7). COMPLETED and CANCELLED are terminal states with no outgoing transitions.
>
> **Transitions into FAILED:** Both `ASSIGNED → FAILED` (early setup failures) and `IN_PROGRESS → FAILED` (runtime crashes) are valid. `FAILED → ASSIGNED` enables reassignment when `retry_count < max_retries`.
>
> **Transitions into INTERRUPTED:** Both `ASSIGNED → INTERRUPTED` and `IN_PROGRESS → INTERRUPTED` are valid (graceful shutdown can occur at any active phase). `INTERRUPTED → ASSIGNED` enables reassignment on restart.

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
  task_structure: "parallel"      # sequential, parallel, mixed (see §6.9)
  coordination_topology: "auto"  # auto, sas, centralized, decentralized, context_dependent (see §6.9)
  budget_limit: 2.00             # max USD for this task
  deadline: null
  max_retries: 1                 # max reassignment attempts after failure (0 = no retry)
  status: "assigned"
  parent_task_id: null           # parent task ID when created via delegation
  delegation_chain: []           # ordered agent IDs of delegators (root first)
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

> **Current state (M3):** `ParallelExecutor` (in `engine/parallel.py`) implements concurrent agent execution with `asyncio.TaskGroup`, configurable concurrency limits, resource locking for exclusive file access, error isolation, and progress tracking. While M3 primarily targets single-agent execution, parallel coordination is implemented here as prerequisite infrastructure for M4 multi-agent workflows. Models in `engine/parallel_models.py`: `AgentAssignment`, `ParallelExecutionGroup`, `AgentOutcome`, `ParallelExecutionResult`, `ParallelProgress`.

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

> **Current state (M4):** All six strategies are implemented behind the `TaskAssignmentStrategy` protocol. Manual, Role-based, Load-balanced, Cost-optimized, and Auction strategies are in the static `STRATEGY_MAP`. Hierarchical requires a `HierarchyResolver` at runtime via `build_strategy_map(hierarchy=...)`. Config-level `TaskAssignmentConfig` validates strategy names against the known set. Scoring-based strategies filter out agents at capacity via `AssignmentRequest.max_concurrent_tasks`. Error signaling contract: `ManualAssignmentStrategy` raises exceptions (`TaskAssignmentError`, `NoEligibleAgentError`); scoring-based strategies return `AssignmentResult(selected=None)`. `TaskAssignmentService` propagates both patterns.

### 6.5 Agent Execution Loop

The agent execution loop defines how an agent processes a task from start to finish. The framework provides multiple configurable loop architectures behind an `ExecutionLoop` protocol, making the system extensible. The default can vary by task complexity, and is configurable per agent or role.

> **Current state (M3):** ReAct (Loop 1) and Plan-and-Execute (Loop 2) are implemented. `ParallelExecutor` enables concurrent `AgentEngine.run()` calls with `TaskGroup` + Semaphore concurrency limits, resource locking, and error isolation (see §6.3). Hybrid loop and auto-selection are M4+.

#### ExecutionLoop Protocol

All loop implementations satisfy the `ExecutionLoop` runtime-checkable protocol (defined in `engine/loop_protocol.py`):

- **`get_loop_type() -> str`** — returns a unique identifier (e.g. `"react"`)
- **`execute(...) -> ExecutionResult`** — runs the loop to completion, accepting `AgentContext`, `CompletionProvider`, optional `ToolInvoker`, optional `BudgetChecker`, optional `ShutdownChecker`, and optional `CompletionConfig`

Supporting models:

- **`TerminationReason`** — enum: `COMPLETED`, `MAX_TURNS`, `BUDGET_EXHAUSTED`, `SHUTDOWN`, `ERROR`
- **`TurnRecord`** — frozen per-turn stats (tokens, cost, tool calls, finish reason)
- **`ExecutionResult`** — frozen outcome with final context, termination reason, turn records, and optional error message (required when reason is `ERROR`)
- **`BudgetChecker`** — callback type `Callable[[AgentContext], bool]` invoked before each LLM call
- **`ShutdownChecker`** — callback type `Callable[[], bool]` checked at turn boundaries to initiate cooperative shutdown

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
2. **Pre-flight budget enforcement** — if `BudgetEnforcer` is provided, check monthly hard stop and daily limit via `check_can_execute()`, then apply auto-downgrade via `resolve_model()`. Raises `BudgetExhaustedError` or `DailyLimitExceededError` on violation.
3. **Build system prompt** — calls `build_system_prompt()` with agent identity and task. Tool definitions are NOT included — they are supplied via the API's `tools` parameter (see D22 below). Follows the **non-inferable-only principle**: system prompts include only information the agent cannot discover by reading the codebase or environment (role constraints, custom conventions, organizational policies). Generic architecture overviews and file structure descriptions are excluded — [research](https://arxiv.org/abs/2602.11988) shows they reduce success rates while increasing costs 20%+.

> **Decision ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D22):** Do NOT list available tools in the system prompt — the API's `tools` parameter already injects richer tool definitions including JSON schemas. The system prompt listing is strictly inferior (no schemas) and wastes 200-400+ tokens per call. Behavioral guidance ("when to use tool X vs Y") may be added later as non-redundant value.
4. **Create context** — `AgentContext.from_identity()` with the configured `max_turns`.
5. **Seed conversation** — injects system prompt, optional memory messages, and formatted task instruction as initial messages.
6. **Transition task** — `ASSIGNED` → `IN_PROGRESS` (pass-through if already `IN_PROGRESS`).
7. **Prepare tools and budget** — creates `ToolInvoker` from registry and `BudgetChecker` from `BudgetEnforcer` (task + monthly + daily limits with pre-computed baselines and alert deduplication) or from task budget limit alone when no enforcer is configured.
8. **Delegate to loop** — calls `ExecutionLoop.execute()` with context, provider, tool invoker, budget checker, and completion config. If `timeout_seconds` is set, wraps the call in `asyncio.wait_for`; on expiry the run returns with `TerminationReason.ERROR` but cost recording and post-execution processing still occur.
9. **Record costs** — records accumulated `TokenUsage` to `CostTracker` (if available). Cost recording failures are logged but do not affect the result.
10. **Apply post-execution transitions** — on `COMPLETED` termination: IN_PROGRESS → IN_REVIEW → COMPLETED (two-hop auto-complete in M3; reviewers deferred to M4+). On `SHUTDOWN` termination: current status → INTERRUPTED (see §6.7). On `ERROR` termination: recovery strategy is applied (default `FailAndReassignStrategy` transitions to FAILED; see §6.6). All other termination reasons (`MAX_TURNS`, `BUDGET_EXHAUSTED`) leave the task in its current state. Transition failures are logged but do not discard the successful execution result.
11. **Return result** — wraps `ExecutionResult` in `AgentRunResult` with engine-level metadata.

Error handling: `MemoryError` and `RecursionError` propagate unconditionally. `BudgetExhaustedError` (including `DailyLimitExceededError`) returns `TerminationReason.BUDGET_EXHAUSTED` without recovery — budget exhaustion is a controlled stop, not a crash. All other exceptions are caught and wrapped in an `AgentRunResult` with `TerminationReason.ERROR`.

Constructor accepts: `provider` (required), `execution_loop` (defaults to `ReactLoop`), `tool_registry`, `cost_tracker`, `recovery_strategy` (defaults to `FailAndReassignStrategy`), `shutdown_checker`, `budget_enforcer`. The `run()` method also accepts `memory_messages` — optional working memory to inject between the system prompt and task instruction (memory retrieval is M5; the engine provides the injection hook).

Logs structured events under the `execution.engine.*` namespace (13 constants in `events/execution.py`): creation, start, prompt built, completion, errors, budget stopped, invalid input, task transitions, cost recording outcomes, task metrics, and timeout.

**`AgentRunResult`** — frozen Pydantic model wrapping `ExecutionResult` with engine metadata:

- `execution_result` — outcome from the execution loop
- `system_prompt` — the `SystemPrompt` used for this run
- `duration_seconds` — wall-clock run time
- `agent_id`, `task_id` — identifiers
- Computed fields: `termination_reason`, `total_turns`, `total_cost_usd`, `is_success`, `completion_summary`

### 6.6 Agent Crash Recovery

When an agent execution fails unexpectedly (unhandled exception, OOM, process kill), the framework needs a recovery mechanism. Recovery strategies are implemented behind a `RecoveryStrategy` protocol, making the system pluggable — new strategies can be added without modifying existing ones.

> **MVP: Fail-and-Reassign only (Strategy 1).** Checkpoint Recovery is M4/M5.

**`RecoveryStrategy` protocol:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `recover` | `async def recover(*, task_execution: TaskExecution, error_message: str, context: AgentContext) -> RecoveryResult` | Apply recovery to a failed task execution |
| `get_strategy_type` | `def get_strategy_type() -> str` | Return strategy type identifier (must not be empty) |

**`RecoveryResult` model (frozen):**

| Field | Type | Description |
|-------|------|-------------|
| `task_execution` | `TaskExecution` | Updated execution after recovery (typically `FAILED`) |
| `strategy_type` | `NotBlankStr` | Strategy identifier |
| `context_snapshot` | `AgentContextSnapshot` | Redacted snapshot (turn count, accumulated cost, message count, max turns — no message contents) |
| `error_message` | `NotBlankStr` | Error that triggered recovery |
| `can_reassign` | `bool` (computed) | `retry_count < task.max_retries` |

#### Strategy 1: Fail-and-Reassign (Default / MVP)

The engine catches the failure at its outermost boundary, logs a redacted `AgentContext` snapshot (turn count, accumulated cost — excluding message contents to avoid leaking sensitive prompts/tool outputs), transitions the task to `FAILED`, and makes it available for reassignment (manual or automatic via the task router).

> **Non-terminal state (implemented in M3):** `FAILED` is a `TaskStatus` variant alongside `CANCELLED`. `FAILED` differs from `CANCELLED` (which is terminal) in that failed tasks are eligible for automatic reassignment. Valid transitions: `IN_PROGRESS → FAILED`, `ASSIGNED → FAILED` (early setup failures), `FAILED → ASSIGNED` (reassignment). See the updated §6.1 lifecycle diagram.

```yaml
crash_recovery:
  strategy: "fail_reassign"            # fail_reassign, checkpoint
```

- Simple, no persistence dependency, M3-ready
- All progress is lost on crash — acceptable for short single-agent tasks in the MVP

On crash:
1. Catch exception at the `AgentEngine` boundary (outermost `try/except` in `AgentEngine.run()`)
2. Log at ERROR with redacted `AgentContextSnapshot` (turn count, accumulated cost, message count, max turns — message contents excluded)
3. Transition `TaskExecution` → `FAILED` with the exception as the failure reason
4. `RecoveryResult.can_reassign` reports whether `retry_count < max_retries`

> **M3 limitation:** The `can_reassign` flag is computed and returned in `RecoveryResult`, but automated reassignment is not yet implemented — the task router (§6.4) will consume this in a later milestone. The caller (task router) is responsible for incrementing `retry_count` when creating the next `TaskExecution`.

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

The engine sets a shutdown event, stops accepting new tasks, and gives in-flight agents a grace period to finish their current turn. Agents check the shutdown event at turn boundaries (between LLM calls, before tool invocations) and exit cooperatively. After the grace period, remaining agents are force-cancelled. **All tasks terminated by shutdown — whether they exited cooperatively or were force-cancelled — are marked `INTERRUPTED`** by the engine layer.

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

> **Non-terminal status (implemented in M3):** `INTERRUPTED` is a `TaskStatus` variant. Unlike `FAILED` (eligible for automatic reassignment) or `CANCELLED` (terminal), `INTERRUPTED` indicates the task was stopped due to process shutdown — regardless of whether the agent exited cooperatively or was force-cancelled — and is eligible for manual or automatic reassignment on restart. Valid transitions: `ASSIGNED → INTERRUPTED`, `IN_PROGRESS → INTERRUPTED`, `INTERRUPTED → ASSIGNED` (reassignment on restart). See the updated §6.1 lifecycle diagram.
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

> **Current state:** The `WorkspaceIsolationStrategy` protocol, `PlannerWorktreeStrategy` (git worktree backend), `MergeOrchestrator` (sequential merge with configurable conflict escalation), and `WorkspaceIsolationService` (lifecycle orchestrator with rollback and best-effort teardown) are implemented in `engine/workspace/`. `_validate_git_ref` raises context-appropriate exception types (`WorkspaceMergeError` in merge, `WorkspaceCleanupError` in teardown) with matching log events. `_run_git` similarly accepts a `log_event` parameter for context-aware timeout logging. Runtime multi-agent coordination using these components is M4+.

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

> **Current state:** Task structure classification (`TaskStructureClassifier`), DAG-based decomposition (`DecompositionService`, `DependencyGraph`, `ManualDecompositionStrategy`), LLM-based decomposition (`LlmDecompositionStrategy` with tool calling and JSON content fallback), status rollup (`StatusRollup`), agent-task scoring (`AgentTaskScorer`), routing (`TaskRoutingService`), and auto topology selection (`TopologySelector`) are implemented in `engine/decomposition/` and `engine/routing/`. Workspace isolation (`PlannerWorktreeStrategy`, `MergeOrchestrator`, `WorkspaceIsolationService`) is implemented in `engine/workspace/`. Runtime multi-agent coordination is M4+.

Empirical research on agent scaling ([Kim et al., 2025](https://arxiv.org/abs/2512.08296) — 180 controlled experiments across 3 LLM families and 4 benchmarks) demonstrates that **task decomposability is the strongest predictor of multi-agent effectiveness** — stronger than team size, model capability, or coordination architecture.

#### Task Structure Classification

Each task carries a `task_structure` field (see §6.2 Task Definition) classifying its decomposability:

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
| `sequential` + few artifacts (≤4) | **Single-agent (SAS)** | Coordination overhead fragments reasoning capacity on sequential tasks |
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

The auto-selector uses task structure, artifact count, and (when available from M5 memory) historical single-agent success rate as inputs. The exact selection logic is an M4 implementation detail — the spec defines the interface and the empirically-grounded heuristics above.

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
│   + Mem0 (initial) / Custom Stack (future)    │
│     See ADR-001                               │
└─────────────────────────────────────────────┘
```

### 7.2 Memory Types

| Type | Scope | Persistence | Example |
|------|-------|-------------|---------|
| **Working** | Current task | None (in-context) | "I'm implementing the auth endpoint" |
| **Episodic** | Past events | Configurable | "Last sprint we chose JWT over sessions" |
| **Semantic** | Knowledge | Long-term | "This project uses Litestar with aiosqlite" |
| **Procedural** | Skills/patterns | Long-term | "Code reviews require 2 approvals here" |
| **Social** | Relationships | Long-term | "The QA lead prefers detailed test plans" |

### 7.3 Memory Levels (Configurable)

```yaml
memory:
  level: "persistent"            # none, session, project, persistent (default: session)
  backend: "mem0"               # mem0, custom, cognee, graphiti (future) — see ADR-001
  storage:
    data_dir: "/data/memory"    # mounted Docker volume path
    vector_store: "qdrant"      # qdrant (embedded), qdrant-external, etc.
    history_store: "sqlite"     # sqlite, postgresql
  options:
    retention_days: null         # null = forever
    max_memories_per_agent: 10000
    consolidation_interval: "daily"  # compress old memories
    shared_knowledge_base: true      # agents can access shared facts (see §7.4)
```

### 7.4 Shared Organizational Memory

Beyond individual agent memory (§7.1–7.3), the framework needs **organizational memory** — company-wide knowledge that all agents can access: policies, conventions, architecture decision records (ADRs), coding standards, and operational procedures. This is not personal episodic memory ("what I did last Tuesday") but institutional knowledge ("we always use Litestar, not Flask").

Shared organizational memory is implemented behind an `OrgMemoryBackend` protocol, making the system highly modular and extensible. New backends can be added without modifying existing ones.

#### Backend 1: Hybrid Prompt + Retrieval (Default / MVP)

Critical rules (5-10 items, e.g., "no commits to main," "all PRs need 2 approvals") are injected into every agent's system prompt. Extended knowledge (ADRs, detailed procedures, style guides) is stored in a queryable store and retrieved on demand at task start.

```yaml
org_memory:
  backend: "hybrid_prompt_retrieval"    # hybrid_prompt_retrieval, graph_rag, temporal_kg
  core_policies:                        # always in system prompt
    - "All code must have 80%+ test coverage"
    - "Use Litestar, not Flask"
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

Organizational knowledge stored as entities + relationships in a knowledge graph. Agents query via graph traversal, enabling multi-hop reasoning: "Litestar is our standard" → linked to → "don't use Flask" → linked to → "exception: data team uses Django for admin."

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

Like GraphRAG but tracks how facts change over time. "We used Flask until March 2026, then switched to Litestar." Agents see current truth but can query history for context.

```yaml
org_memory:
  backend: "temporal_kg"
  temporal:
    track_changes: true
    history_retention_days: null        # null = forever
```

- Handles policy evolution naturally. Agents understand when and why things changed
- Most complex. Potentially overkill for small companies or local-first use

> **Extensibility:** All backends implement the `OrgMemoryBackend` protocol (`query(OrgMemoryQuery) → tuple[OrgFact, ...]`, `write(OrgFactWriteRequest, *, author: OrgFactAuthor) → NotBlankStr`, `list_policies() → tuple[OrgFact, ...]`, plus `connect`/`disconnect`/`health_check`/`is_connected`/`backend_name` lifecycle). The MVP ships with Backend 1; Backends 2 and 3 are research directions that may be explored if the default approach proves insufficient. The selected memory layer backend Mem0 (ADR-001) provides optional graph memory via Neo4j/FalkorDB, which could reduce implementation effort for Backends 2-3.
> **Write access control:** Core policies are human-only. ADRs and procedures can be written by senior+ agents. All writes are versioned and auditable. This prevents agents from corrupting shared organizational knowledge while allowing senior agents to document decisions.

### 7.5 Memory Backend Protocol

Agent memory (§7.1–7.4) is implemented behind a pluggable `MemoryBackend` protocol (Mem0 initial, custom stack future — ADR-001). Application code depends only on the protocol; the storage engine is an implementation detail swappable via config.

#### Enums

| Enum | Values | Purpose |
|------|--------|---------|
| `MemoryCategory` | WORKING, EPISODIC, SEMANTIC, PROCEDURAL, SOCIAL | Memory type categories (§7.2) |
| `MemoryLevel` | PERSISTENT, PROJECT, SESSION, NONE | Persistence level per agent (§7.3) |
| `ConsolidationInterval` | HOURLY, DAILY, WEEKLY, NEVER | How often old memories are compressed |

#### MemoryBackend Protocol

```python
@runtime_checkable
class MemoryBackend(Protocol):
    """Lifecycle + CRUD for agent memory storage."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def health_check(self) -> bool: ...

    @property
    def is_connected(self) -> bool: ...
    @property
    def backend_name(self) -> NotBlankStr: ...

    async def store(self, agent_id: NotBlankStr, request: MemoryStoreRequest) -> NotBlankStr: ...
    async def retrieve(self, agent_id: NotBlankStr, query: MemoryQuery) -> tuple[MemoryEntry, ...]: ...
    async def get(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> MemoryEntry | None: ...
    async def delete(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> bool: ...
    async def count(self, agent_id: NotBlankStr, *, category: MemoryCategory | None = None) -> int: ...
```

#### MemoryCapabilities Protocol

Backends that implement `MemoryCapabilities` expose what features they support, enabling runtime capability checks before attempting operations.

```python
@runtime_checkable
class MemoryCapabilities(Protocol):
    """Capability discovery for memory backends."""

    @property
    def supported_categories(self) -> frozenset[MemoryCategory]: ...
    @property
    def supports_graph(self) -> bool: ...
    @property
    def supports_temporal(self) -> bool: ...
    @property
    def supports_vector_search(self) -> bool: ...
    @property
    def supports_shared_access(self) -> bool: ...
    @property
    def max_memories_per_agent(self) -> int | None: ...
```

#### SharedKnowledgeStore Protocol

Backends that support cross-agent shared knowledge implement this protocol alongside `MemoryBackend`. Not all backends need cross-agent queries — this keeps the base protocol clean.

```python
@runtime_checkable
class SharedKnowledgeStore(Protocol):
    """Cross-agent shared knowledge operations."""

    async def publish(self, agent_id: NotBlankStr, request: MemoryStoreRequest) -> NotBlankStr: ...
    async def search_shared(self, query: MemoryQuery, *, exclude_agent: NotBlankStr | None = None) -> tuple[MemoryEntry, ...]: ...
    async def retract(self, agent_id: NotBlankStr, memory_id: NotBlankStr) -> bool: ...
```

#### Error Hierarchy

All memory errors inherit from `MemoryError` so callers can catch the entire family with a single except clause.

| Error | When Raised |
|-------|------------|
| `MemoryError` | Base exception for all memory operations |
| `MemoryConnectionError` | Backend connection cannot be established or is lost |
| `MemoryStoreError` | A store or delete operation fails |
| `MemoryRetrievalError` | A retrieve, search, or count operation fails |
| `MemoryNotFoundError` | A specific memory ID is not found |
| `MemoryConfigError` | Memory configuration is invalid |
| `MemoryCapabilityError` | An unsupported operation is attempted for a backend |

#### Configuration

```yaml
memory:
  backend: "mem0"
  level: "persistent"              # none, session, project, persistent (default: session)
  storage:
    data_dir: "/data/memory"
    vector_store: "qdrant"
    history_store: "sqlite"
  options:
    retention_days: null            # null = forever
    max_memories_per_agent: 10000
    consolidation_interval: "daily"
    shared_knowledge_base: true
```

Configuration is modeled by `CompanyMemoryConfig` (top-level), `MemoryStorageConfig` (storage paths/backends), and `MemoryOptionsConfig` (behaviour tuning). All are frozen Pydantic models. The `create_memory_backend(config)` factory returns an isolated `MemoryBackend` instance per company.

#### Consolidation & Retention Configuration

Memory consolidation, retention enforcement, and archival are configured via frozen Pydantic models in `memory/consolidation/config.py`:

| Config | Purpose |
|--------|---------|
| `ConsolidationConfig` | Top-level: `max_memories_per_agent` limit, nested `retention` and `archival` sub-configs |
| `RetentionConfig` | Per-category `RetentionRule` tuples (category + retention_days), optional `default_retention_days` fallback |
| `ArchivalConfig` | Enables/disables archival of consolidated entries to `ArchivalStore` |

Note: Retention is currently per-category, not per-agent. Per-agent retention overrides are a scope gap to be addressed in a future iteration.

### 7.6 Operational Data Persistence

Agent memory (§7.1–7.5) is handled by the `MemoryBackend` protocol (Mem0 initial, custom stack future — ADR-001). **Operational data** — tasks, cost records, messages, audit logs — is a separate concern managed by a pluggable `PersistenceBackend` protocol. Application code depends only on repository protocols; the storage engine is an implementation detail swappable via config.

```text
┌──────────────────────────────────────────────────────────────────┐
│                     Application Code                              │
│  engine/  budget/  communication/  security/                      │
│     │        │           │             │                          │
│     ▼        ▼           ▼             ▼                          │
│  ┌──────┐ ┌──────┐ ┌──────────┐ ┌──────────┐                    │
│  │ Task │ │ Cost │ │ Message  │ │  Audit   │  ← Repository       │
│  │ Repo │ │ Repo │ │  Repo    │ │  Repo    │    Protocols        │
│  └──┬───┘ └──┬───┘ └────┬─────┘ └────┬─────┘                    │
│     └────────┴──────────┴────────────┘                            │
│                      │                                            │
│  ┌───────────────────┴───────────────────────────────────────┐   │
│  │              PersistenceBackend (protocol)                  │   │
│  │  connect() · disconnect() · health_check() · migrate()     │   │
│  └───────────────────┬───────────────────────────────────────┘   │
│                      │                                            │
│  ┌───────────────────┴───────────────────────────────────────┐   │
│  │  SQLitePersistenceBackend (initial)                         │   │
│  │  PostgresPersistenceBackend (future)                        │   │
│  │  MariaDBPersistenceBackend (future)                         │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

#### Protocol Design

```python
@runtime_checkable
class PersistenceBackend(Protocol):
    """Lifecycle management for operational data storage."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def health_check(self) -> bool: ...
    async def migrate(self) -> None: ...

    @property
    def is_connected(self) -> bool: ...
    @property
    def backend_name(self) -> NotBlankStr: ...

    @property
    def tasks(self) -> TaskRepository: ...
    @property
    def cost_records(self) -> CostRecordRepository: ...
    @property
    def messages(self) -> MessageRepository: ...
    # ... plus lifecycle_events, task_metrics, collaboration_metrics,
    #     parked_contexts, audit_entries
```

Each entity type has its own repository protocol:

```python
@runtime_checkable
class TaskRepository(Protocol):
    """CRUD + query interface for Task persistence."""

    async def save(self, task: Task) -> None: ...
    async def get(self, task_id: str) -> Task | None: ...
    async def list_tasks(self, *, status: TaskStatus | None = None, assigned_to: str | None = None, project: str | None = None) -> tuple[Task, ...]: ...
    async def delete(self, task_id: str) -> bool: ...

@runtime_checkable
class CostRecordRepository(Protocol):
    """CRUD + aggregation interface for CostRecord persistence."""

    async def save(self, record: CostRecord) -> None: ...
    async def query(self, *, agent_id: str | None = None, task_id: str | None = None) -> tuple[CostRecord, ...]: ...
    async def aggregate(self, *, agent_id: str | None = None) -> float: ...

@runtime_checkable
class MessageRepository(Protocol):
    """CRUD + query interface for Message persistence."""

    async def save(self, message: Message) -> None: ...
    async def get_history(self, channel: str, *, limit: int | None = None) -> tuple[Message, ...]: ...
```

#### Configuration

```yaml
persistence:
  backend: "sqlite"                   # sqlite, postgresql, mariadb (future)
  sqlite:
    path: "/data/ai-company.db"       # database file path (mounted volume in Docker)
    wal_mode: true                    # WAL for concurrent read performance
    journal_size_limit: 67108864      # 64 MB WAL journal limit
  # postgresql:                       # future
  #   url: "postgresql://user:pass@host:5432/ai_company"
  #   pool_size: 10
  # mariadb:                          # future
  #   url: "mariadb://user:pass@host:3306/ai_company"
  #   pool_size: 10
```

#### Entities Persisted

| Entity | Source Module | Repository | Key Queries |
|--------|-------------|------------|-------------|
| `Task` | `core/task.py` | `TaskRepository` | by status, by assignee, by project |
| `CostRecord` | `budget/cost_record.py` | `CostRecordRepository` | by agent, by task, aggregations |
| `Message` | `communication/message.py` | `MessageRepository` | by channel |
| `AuditEntry` | `security/models.py` | `AuditRepository` | by agent, by action type, by verdict, by risk level, time range |
| `ParkedContext` | `security/timeout/parked_context.py` | `ParkedContextRepository` | by execution_id, by agent_id, by task_id |
| Agent runtime state (planned — M7) | `engine/` | `AgentStateRepository` (planned) | by agent_id, active agents |

#### Migration Strategy

- Migrations run programmatically at startup via `PersistenceBackend.migrate()`
- Initial migration creates all tables
- Versioned migrations implemented per-backend (e.g. `persistence/sqlite/migrations.py` for SQLite)
- SQLite uses `user_version` pragma for version tracking; PostgreSQL/MariaDB use a migrations table

#### Key Principles

- **App code never imports a concrete backend** — only repository protocols
- **Adding a new backend** requires implementing `PersistenceBackend` + all repository protocols — no changes to consumers
- **Same entity models everywhere** — repositories accept and return the existing frozen Pydantic models (Task, CostRecord, Message), no ORM models or data transfer objects
- **Async throughout** — all repository methods are async, matching the project's concurrency model

#### Multi-Tenancy

Each company gets its own database. The `PersistenceConfig` embedded in a company's `RootConfig` specifies the backend type and connection details (e.g. a unique SQLite file path or PostgreSQL database URL). The `create_backend(config)` factory returns an isolated `PersistenceBackend` instance per company — no shared state, no cross-company data leakage.

```python
# One database per company — configured in each company's YAML
company_a_backend = create_backend(company_a_config.persistence)
company_b_backend = create_backend(company_b_config.persistence)
# Each backend has independent lifecycle: connect → migrate → use → disconnect
```

#### Future: Runtime Backend Switching

Runtime backend switching (e.g. migrating a company from SQLite to PostgreSQL during operation) is a planned future capability. The protocol-based design already supports this — the engine would disconnect the current backend, connect a new one with different config, and migrate. Implementation details (data migration tooling, zero-downtime switchover, connection draining) are deferred to the PostgreSQL backend milestone.

### 7.7 Memory Injection Strategies

Agent memory reaches agents through pluggable injection strategies behind
the `MemoryInjectionStrategy` protocol. The strategy determines *how*
memories are surfaced to the agent during execution.

#### Strategy 1: Context Injection (Default / MVP)

Pre-retrieves relevant memories before execution, ranks by
relevance+recency, enforces token budget, formats as ChatMessage(s)
injected between system prompt and task instruction. Agent passively
receives memories.

> **Non-inferable filter:** Retrieved memories should be filtered before injection to exclude content the agent can discover by reading the codebase or environment. Only inject memories containing non-inferable information: prior decisions, learned conventions, interpersonal context, historical outcomes. [Research](https://arxiv.org/abs/2602.11988) shows generic context increases cost 20%+ with minimal success improvement; LLM-generated context can actually reduce success rates.
>
> **Decision ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D23):** Pluggable `MemoryFilterStrategy` protocol. Initial: tag-based at write time. Define `non-inferable` tag convention with advisory validation at `MemoryBackend.store()` boundary (warns on missing tags, never blocks). System prompt instructs agents what qualifies: design rationale, team decisions, "why not X", cross-repo knowledge = non-inferable; code structure, API signatures, file contents = inferable. Uses existing `MemoryMetadata.tags` and `MemoryQuery.tags` — zero new models needed. Future strategies: LLM classification at retrieval, keyword/pattern heuristic.

Pipeline: `MemoryBackend.retrieve()` -> rank by relevance+recency ->
filter by min_relevance -> apply `MemoryFilterStrategy` (D23, optional) ->
greedy token-budget packing -> format as ChatMessage (configured role:
SYSTEM or USER) with delimiters.

Ranking algorithm:
1. `relevance = entry.relevance_score ?? config.default_relevance`
2. Personal entries: `relevance = min(relevance + personal_boost, 1.0)`
3. `recency = exp(-decay_rate * age_hours)`
4. `combined = relevance_weight * relevance + recency_weight * recency`
5. Filter: `combined >= min_relevance`
6. Sort descending by `combined_score`

Shared memories (from `SharedKnowledgeStore`) are fetched in parallel,
merged with personal memories (no personal_boost for shared), and
ranked together.

#### Strategy 2: Tool-Based Retrieval (Future)

Agent has `recall_memory` / `search_memory` tools it calls on-demand
during execution. Agent actively decides when and what to remember.
More token-efficient (only retrieves when needed) but consumes
tool-call turns and requires agent discipline to invoke.

#### Strategy 3: Self-Editing Memory (Future)

Agent has structured memory blocks (core, archival, recall) it reads
AND writes during execution via dedicated tools. Core memory always
in context, archival/recall searched via tools. Most sophisticated
(Letta/MemGPT-inspired) but highest complexity and LLM overhead.

#### Protocol

All strategies implement `MemoryInjectionStrategy`:
- `prepare_messages(agent_id, query_text, token_budget) -> tuple[ChatMessage, ...]`
- `get_tool_definitions() -> tuple[ToolDefinition, ...]`
- `strategy_name -> str`

Strategy selection via config: `memory.retrieval.strategy: context | tool_based | self_editing`

---

## 8. HR & Workforce Management

> **MVP: Not in M3–M4.** HR features (hiring, firing, performance tracking, promotions) are M5–M7. Agent workforce is configured manually via YAML in early milestones.

> **Implementation note (M7):** Hiring pipeline (`HiringService`), offboarding pipeline
> (`OffboardingService`), onboarding checklists (`OnboardingService`), and agent registry
> (`AgentRegistryService`) are now implemented. Performance tracking subsystem
> (`hr/performance/`) complete with pluggable quality scoring, collaboration scoring,
> trend detection, and multi-window aggregation. Promotions/demotions (section 8.4)
> are implemented in `hr/promotion/` — ThresholdEvaluator (D13), SeniorityApprovalStrategy
> (D14), SeniorityModelMapping (D15), PromotionService orchestrator.

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

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D8):**
>
> - **D8.1 — Source:** Templates + LLM customization. Templates for common roles (reuses existing template system §14.1). LLM generates config for novel roles not covered by templates. Approval gate catches invalid/bad configs before instantiation.
> - **D8.2 — Persistence:** Operational store via `PersistenceBackend` (§7.6). YAML stays as bootstrap seed — operational store wins for runtime state. Enables rehiring, auditable history.
> - **D8.3 — Hot-plug:** Agents are hot-pluggable at runtime via a dedicated company/registry service (not `AgentEngine`, which remains the per-agent task runner). Thread-safe registry, wired into message bus + tools + budget.

### 8.2 Firing / Offboarding

1. Triggered by: budget cuts, poor performance metrics, project completion, human decision
2. Agent's memory is archived (not deleted)
3. Active tasks are reassigned
4. Team is notified

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D9, D10):**
>
> - **D9 — Task Reassignment:** Pluggable `TaskReassignmentStrategy` protocol. Initial: queue-return — tasks return to unassigned queue, existing `TaskRoutingService` (§6.4) re-routes with priority boost for reassigned tasks. Future strategies: same-department/lowest-load, manager-decides (LLM), HR agent decides.
> - **D10 — Memory Archival:** Pluggable `MemoryArchivalStrategy` protocol. Initial: full snapshot, read-only. Pipeline: retrieve all → archive to `ArchivalStore` → selectively promote semantic+procedural to `OrgMemoryBackend` (rule-based) → clean hot store → mark TERMINATED. Rehiring = restore archived memories into new `AgentIdentity`. Future strategies: selective discard, full-accessible.

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

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D2, D3, D11, D12):**
>
> - **D2 — Quality Scoring:** Pluggable `QualityScoringStrategy` protocol. Initial: layered combination — (1) FREE: objective CI signals (test pass/fail, lint, coverage delta), (2) ~$1/day: small-model LLM judge (different family than agent) evaluates output vs acceptance criteria, (3) on-demand: human override via API, highest weight. Start with Layer 1 only; add layers incrementally. Future strategies: CI-only, LLM-only, human-only.
> - **D3 — Collaboration Scoring:** Pluggable `CollaborationScoringStrategy` protocol. Initial: automated behavioral telemetry — `collaboration_score = weighted_average(delegation_success_rate, delegation_response_latency, conflict_resolution_constructiveness, meeting_contribution_rate, loop_prevention_score, handoff_completeness)`. Weights configurable per-role. Optional: periodic LLM sampling (1%) for calibration + human override via API. Future strategies: LLM evaluation, peer ratings, human-provided.
> - **D11 — Rolling Windows:** Pluggable `MetricsWindowStrategy` protocol. Initial: multiple simultaneous windows — 7d (acute regressions), 30d (sustained patterns), 90d (baseline/drift). Min 5 data points per window; below that, report "insufficient data." Future strategies: fixed single window, per-metric configurable.
> - **D12 — Trend Detection:** Pluggable `TrendDetectionStrategy` protocol. Initial: Theil-Sen regression slope per window + configurable thresholds classify as improving/stable/declining. Theil-Sen has 29.3% outlier breakdown (tolerates ~1 in 3 bad data points). Min 5 data points. Future strategies: period-over-period, OLS regression, threshold-only.

### 8.4 Promotions & Demotions

Agents can move between seniority levels based on performance:
- Promotion criteria: sustained high quality scores, task complexity handled, peer feedback
- Demotion criteria: repeated failures, quality drops, cost inefficiency
- Promotions can unlock higher tool access levels (see Progressive Trust)
- Model upgrades/downgrades may accompany level changes (configurable)

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D13, D14, D15):**
>
> - **D13 — Promotion Criteria:** Pluggable `PromotionCriteriaStrategy` protocol. Initial: configurable threshold gates. `ThresholdEvaluator` with `min_criteria_met: int` (N of M) + `required_criteria: list[str]`. Setting min=total gives AND; min=1 gives OR. Default: junior→mid = 2 of 3 criteria, mid→senior = all. Future strategies: pure AND, pure OR.
> - **D14 — Promotion Approval:** Pluggable `PromotionApprovalStrategy` protocol. Initial: senior+ requires human approval. Junior→mid auto-promotes (low cost impact: small→medium ~4x). Demotions: auto-apply for cost-saving (model downgrade), human approval for authority-reducing demotions. Future strategies: all-human, configurable-per-level.
> - **D15 — Model Mapping:** Pluggable `ModelMappingStrategy` protocol. Initial: default ON — `hr.promotions.model_follows_seniority: true`. Model changes at task boundaries only (never mid-execution, consistent with auto-downgrade §10.4). Per-agent `preferred_model` overrides seniority default. Smart routing (§9.4) still uses cheap models for simple tasks regardless of seniority. Future strategies: always-applied, opt-in-only.

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

> **Implementation note (M5):** `ProviderConfig` now includes `subscription: SubscriptionConfig` and `degradation: DegradationConfig` fields for per-provider quota limits and subscription-aware degradation behavior. The default degradation strategy is `ALERT` (raise `QuotaExhaustedError`). `FALLBACK` (route to fallback providers) and `QUEUE` (delay and retry) strategies are defined in the model but **not yet implemented** — the engine currently always raises on quota exhaustion regardless of strategy. Regular quota polling / proactive alerting before quotas are hit is deferred to a follow-up issue.

### 9.3 LiteLLM Integration

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

> **MVP: Not in M3.** Budget tracking and per-task cost recording exist (M2); cost controls (§10.4) are now enforced by `BudgetEnforcer` (a service the engine composes, not an agent — M5). The CFO agent is M5+.

The CFO agent (when enabled) acts as a cost management system:

- Monitors real-time spending across all agents
- Alerts when departments approach budget limits
- Suggests model downgrades when budget is tight
- Reports daily/weekly spending summaries
- Recommends hiring/firing based on cost efficiency
- Blocks tasks that would exceed remaining budget
- Optimizes model routing for cost/quality balance

> **Implementation note (M5):** `CostOptimizer` service (`budget/optimizer.py`)
> implements anomaly detection (sigma + spike factor), per-agent efficiency
> analysis, model downgrade recommendations (via `ModelResolver`), routing
> optimization suggestions (cost + context-window comparison), and operation
> approval evaluation. `ReportGenerator` service (`budget/reports.py`)
> produces multi-dimensional spending reports with task/provider/model
> breakdowns and period-over-period comparison.

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
  reset_day: 1
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

> **Implementation note (M5):** `BudgetEnforcer` composes `CostTracker` +
> `BudgetConfig` + optional `QuotaTracker` + optional `ModelResolver` to
> provide three enforcement layers: (1) pre-flight checks via
> `check_can_execute` (monthly hard stop + per-agent daily limit + provider
> quota enforcement when `QuotaTracker` is present), (2) in-flight budget
> checking via a sync `BudgetChecker` closure with pre-computed baselines
> (task + monthly + daily limits, alert deduplication), and (3)
> task-boundary auto-downgrade via `resolve_model`. Billing periods are
> scoped by `billing_period_start(reset_day)`. `DailyLimitExceededError`
> is a subclass of `BudgetExhaustedError` for granular error handling.

### 10.5 LLM Call Analytics

> **Current state:** Proxy metrics (M3), call categorization + coordination metric data models (M4 models, brought forward), and error taxonomy classification pipeline (M5) are implemented. Runtime collection pipeline for coordination metrics and full analytics layer are M5+.

Every LLM provider call is tracked with comprehensive metadata for financial reporting, debugging, and orchestration overhead analysis. The analytics system builds incrementally across milestones.

#### M3: Per-Call Tracking + Proxy Overhead Metrics

Every completion call produces a `CompletionResponse` with `TokenUsage` (token counts and cost). The engine layer creates a `CostRecord` (with agent/task context) and records it into `CostTracker` — the provider itself does not have agent/task context. In M3, the engine additionally logs **proxy overhead metrics** at task completion:

- `turns_per_task` — number of LLM turns to complete the task (from `AgentRunResult.total_turns`)
- `tokens_per_task` — total tokens consumed (from `AgentContext.accumulated_cost.total_tokens`)
- `cost_per_task` — total USD cost (from `AgentContext.accumulated_cost.cost_usd` via `AgentRunResult.total_cost_usd`)
- `duration_seconds` — wall-clock execution time in seconds (from `AgentRunResult.duration_seconds`)
- `prompt_tokens` — estimated system prompt tokens (from `SystemPrompt.estimated_tokens`)
- `prompt_token_ratio` — ratio of prompt tokens to total tokens (overhead indicator, `@computed_field`; warns when >0.3)

These are natural overhead indicators — a task consuming 15 turns and 50k tokens for a one-line fix signals a problem.

These metrics are captured in `TaskCompletionMetrics` (in `engine/metrics.py`), a frozen Pydantic model with a `from_run_result()` factory method. The engine logs these metrics at task completion via the `EXECUTION_ENGINE_TASK_METRICS` event.

#### M4: Call Categorization + Orchestration Ratio

> **Current state:** Data models (`LLMCallCategory`, `CategoryBreakdown`, `OrchestrationRatio`, `CostRecord.call_category`) and query methods (`CostTracker.get_category_breakdown`, `get_orchestration_ratio`) are implemented. Runtime categorization logic (automatic tagging of calls during multi-agent execution) is deferred to M4 runtime integration.

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

> **Current state:** Error taxonomy classification pipeline is implemented in `engine/classification/`. Four heuristic-based detectors (logical contradiction, numerical drift, context omission, coordination failure) run post-execution when enabled via `error_taxonomy_config`. Integrated into `AgentEngine`. Classification results are log-only; programmatic access is planned for a future milestone. Full semantic analysis detectors are planned for future milestones.

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
| **File System** | Read, write, edit, list, delete files | All developers, writers |
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

**Permission checking (M3):** Each `BaseTool` carries a `category: ToolCategory` attribute used for access-level gating. `ToolInvoker` accepts an optional `ToolPermissionChecker` which enforces the agent's `ToolPermissions.access_level` (see §11.2). Permission checking occurs after tool lookup but before parameter validation:

1. `get_permitted_definitions()` filters tool definitions sent to the LLM — the agent only sees tools it is permitted to use.
2. At invocation time, denied tools return `ToolResult(is_error=True)` with a descriptive denial reason (defense-in-depth against LLM hallucinating unpresented tools).

The `ToolPermissionChecker` resolves permissions using a priority-based system: denied list (highest) → allowed list → access-level categories → deny (default). `AgentEngine._make_tool_invoker()` creates a permission-aware invoker from the agent's `ToolPermissions` at the start of each `run()` call. Note: M3 implements category-level gating only; the granular sub-constraints described in §11.2 (workspace scope, network mode) are planned for when sandboxing is implemented.

> **M3 implementation note — Built-in git tools:** Six workspace-scoped git tools are implemented in `tools/git_tools.py` with a shared `_BaseGitTool` base class in `tools/_git_base.py`: `GitStatusTool`, `GitLogTool`, `GitDiffTool`, `GitBranchTool`, `GitCommitTool`, and `GitCloneTool`. The base class enforces workspace boundary security (path traversal prevention via `resolve()` + `relative_to()`) and provides a common `_run_git()` helper using `asyncio.create_subprocess_exec` (never `shell=True`). Security hardening includes: `GIT_TERMINAL_PROMPT=0` to prevent credential prompts, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=os.devnull`, and `GIT_PROTOCOL_FROM_USER=0` to restrict config/protocol attack surfaces, rejection of flag-like argument values (starting with `-`) for refs, branch names, author filters, date strings, and other git arguments, URL scheme validation on clone (only `https://`, `ssh://`, `git://`, and SCP-like syntax — plain `http://` rejected for security) with `--` separator before positional URL argument, and clone URLs starting with `-` are rejected. All tools return `ToolExecutionResult` for errors rather than raising exceptions. When a `SandboxBackend` is injected, `_run_git()` delegates subprocess management to the sandbox via `_run_git_sandboxed()` — the sandbox handles environment filtering and workspace-scoped cwd enforcement, while `_validate_path` independently enforces workspace boundaries for git path arguments. Git hardening env vars are passed as `env_overrides` to the sandbox, and `SandboxResult` is converted to `ToolExecutionResult` via `_sandbox_result_to_execution_result`. Without a sandbox, the direct-subprocess path is used (backward compatible). Both paths explicitly close the subprocess transport on Windows (via `tools/_process_cleanup.py`) to prevent `ResourceWarning` on `ProactorEventLoop`. **Future:** Consider adding host/IP allowlisting for clone URLs to prevent SSRF against internal networks (loopback, link-local, private ranges).

### 11.1.2 Tool Sandboxing

Tool execution requires safety boundaries proportional to the risk of each tool category. The framework uses a **layered sandboxing strategy** with a pluggable `SandboxBackend` protocol — new backends can be added without modifying existing ones. The default configuration uses lighter isolation for low-risk tools and stronger isolation for high-risk tools.

> **MVP: Subprocess sandbox for file/git tools. Docker optional for code execution.** K8s is future.
>
> **Decision ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D16):** Docker MVP only via `aiodocker` (async-native, Python 3.14 support). Pre-built image (Python 3.14 + Node.js LTS + basic utils, <500MB) + user-configurable via `docker.image` config. **Fail with clear error** if Docker unavailable — no unsafe subprocess fallback for code execution (file/git tools already use `SubprocessSandbox`). gVisor (`--runtime=runsc`) as free config-level hardening upgrade. Evaluate WASM/Firecracker post-M7. `SandboxBackend` protocol makes adding backends trivial.

#### Sandbox Backends

| Backend | Isolation | Latency | Dependencies | Status |
|---------|-----------|---------|--------------|--------|
| `SubprocessSandbox` | Process-level: env filtering (allowlist + denylist), restricted PATH (configurable via `extra_safe_path_prefixes`), workspace-scoped cwd, timeout + process-group kill, library injection var blocking, explicit transport cleanup on Windows | ~ms | None | **Implemented** |
| `DockerSandbox` | Container-level: ephemeral container, mounted workspace, no network, resource limits (CPU/memory/time) | ~1-2s cold start | Docker | **Implemented** |
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
    mount_mode: "ro"                   # read-only by default; workspace mounted separately
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

### 11.1.3 MCP Integration

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D17, D18):**
>
> - **D17 — MCP SDK:** Official `mcp` Python SDK, pinned `==1.26.0`. Thin `MCPBridgeTool` adapter layer isolates the rest of the codebase from SDK API changes. Support **stdio** (local/dev) and **Streamable HTTP** (remote/production) transports. Skip deprecated SSE. v2 migration planned — pin range prevents accidental breaking upgrade.
> - **D18 — MCP Result Mapping:** Adapter in `MCPBridgeTool` keeps `ToolResult` as-is. Mapping: text blocks → concatenate to `content: str`; image/audio → `[image: {mimeType}]` placeholder + base64 in `metadata["attachments"]`; `structuredContent` → `metadata["structured_content"]`; `isError` → `is_error` (1:1). Future: extend `ToolResult` with optional `attachments` when multi-modal LLM tool results are needed.

### 11.1.4 Action Type System

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D1):**
>
> Action types classify agent actions for use by autonomy presets (§12.2), SecOps validation (§12.3), tiered timeout policies (§12.4), and progressive trust (§11.3). Three sub-decisions:
>
> - **D1.1 — Registry:** `StrEnum` for ~25 built-in action types (type safety, autocomplete, typos caught at compile time) + `ActionTypeRegistry` for custom types via explicit registration. Unknown strings rejected at config load time. Critical for security — a typo in `human_approval` list silently means "skip approval."
> - **D1.2 — Granularity:** Two-level `category:action` hierarchy. Category shortcuts: `auto_approve: ["code"]` expands to all `code:*` actions. Fine-grained: `human_approval: ["code:create"]`.
>
> **Proposed taxonomy (~25 leaf types):**
>
> ```text
> code:read, code:write, code:create, code:delete, code:refactor
> test:write, test:run
> docs:write
> vcs:read, vcs:commit, vcs:push, vcs:branch
> deploy:staging, deploy:production
> comms:internal, comms:external
> budget:spend, budget:exceed
> org:hire, org:fire, org:promote
> db:query, db:mutate, db:admin
> arch:decide
> ```
>
> - **D1.3 — Classification:** Static tool metadata. Each `BaseTool` declares its `action_type`. Default mapping from `ToolCategory` → action type. Non-tool actions (`org:hire`, `budget:spend`) triggered by engine-level operations. No LLM in the security classification path.

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

> **M3 implementation note:** The current `ToolPermissionChecker` implements **category-level gating only** — each access level maps to a set of permitted `ToolCategory` values (e.g., `STANDARD` permits `file_system`, `code_execution`, `version_control`, `web`, `terminal`, `analytics`). `SubprocessSandbox` provides workspace-scoped cwd enforcement and env filtering (see §11.1.2). The granular sub-constraints shown above (network mode, containerization) are planned for Docker/K8s sandbox backends.

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
      auto_approve: ["code", "test", "docs", "comms:internal"]
      human_approval: ["deploy", "comms:external", "budget:exceed", "org:hire"]
      security_agent: true

    supervised:
      description: "Human approves major steps. Agents handle details."
      auto_approve: ["code:write", "comms:internal"]
      human_approval: ["arch", "code:create", "deploy", "vcs:push"]
      security_agent: true

    locked:
      description: "Human must approve every action."
      auto_approve: []
      human_approval: ["all"]
      security_agent: true        # still runs for audit logging, but human is approval authority
```

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D6, D7):**
>
> - **D6 — Autonomy Scope:** Three-level resolution chain: per-agent → per-department → company default. Optional `autonomy_level` on `AgentIdentity` and department config. Resolution: `agent.autonomy_level or department.autonomy_level or company.autonomy.level`. Seniority validation: Juniors/Interns cannot be set to `full`.
> - **D7 — Autonomy Changes at Runtime:** Pluggable `AutonomyChangeStrategy` protocol. Initial: **(a+c hybrid)** — human-only promotion via REST API (no agent including CEO can escalate privileges) **plus** automatic downgrade on: high error rate → one level down, budget exhausted → supervised, security incident → locked. Recovery from auto-downgrade: human-only. Precedent: no real-world security system automatically grants higher privileges. Future strategies: fully configurable conditions.

### 12.3 Security Operations Agent

A special meta-agent that reviews all actions before execution:

- Evaluates safety of proposed actions
- Checks for data leaks, credential exposure, destructive operations
- Validates actions against company policies
- Maintains an audit log of all approvals/denials
- Escalates uncertain cases to human queue with explanation
- **Cannot be overridden by other agents** (only human can override)

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D4, D5):**
>
> - **D4 — LLM vs Rule-based:** Hybrid approach. Rule engine for known patterns (credentials, path traversal, destructive ops) — sub-ms, covers ~95% of cases. LLM fallback only for uncertain cases (~5%). Full autonomy mode: rules + audit logging only, no LLM path. Hard safety rules (credential exposure, data destruction) **never bypass** regardless of autonomy level. Precedent: AWS GuardDuty, LlamaFirewall, NeMo Guardrails all use hybrid.
> - **D5 — Integration Point:** Pluggable `SecurityInterceptionStrategy` protocol. Initial: before every tool invocation — slots into existing `ToolInvoker` between permission check and tool execution. Policy strictness (not interception point) configurable per autonomy level. Add post-tool-call scanning for sensitive data in outputs. Performance: sub-ms rule check is invisible against seconds of LLM inference. Future strategies: batch-level (before task step), assignment-only.

#### Output Scan Response Policies

After the output scanner detects sensitive data, a pluggable **`OutputScanResponsePolicy`** protocol decides how to handle the findings. Four built-in policies ship behind the protocol:

| Policy | Behavior | Default for |
|--------|----------|-------------|
| **Redact** (default) | Return scanner's redacted content as-is | `SEMI`, `SUPERVISED` autonomy |
| **Withhold** | Clear redacted content — fail-closed, no partial data returned | `LOCKED` autonomy |
| **Log-only** | Discard findings (logs at WARNING), pass original output through | `FULL` autonomy |
| **Autonomy-tiered** | Delegate to a sub-policy based on effective autonomy level | Composite policy |

Policy selection is declarative via `SecurityConfig.output_scan_policy_type` (`OutputScanPolicyType` enum). A factory function (`build_output_scan_policy`) resolves the enum to a concrete policy instance. Runtime constructor injection on `SecOpsService` is also supported for full flexibility. The policy is applied *after* audit recording, preserving audit fidelity regardless of policy outcome.

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
      actions: ["code:write", "comms:internal", "test"]
    medium_risk:
      timeout_minutes: 240
      on_timeout: "deny"             # auto-deny medium-risk after 4 hours
      actions: ["code:create", "vcs:push", "arch:decide"]
    high_risk:
      timeout_minutes: null          # wait forever
      on_timeout: "wait"
      actions: ["deploy", "db:admin", "comms:external", "org:hire"]
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

> **Decisions ([ADR-002](docs/decisions/ADR-002-design-decisions-batch-1.md) D19, D20, D21):**
>
> - **D19 — Risk Tier Classification:** Pluggable `RiskTierClassifier` protocol. Initial: configurable YAML mapping — `RiskTierMapping` config model with `dict[str, ApprovalRiskLevel]`. Sensible defaults matching examples above (e.g. `code:write` → low, `deploy:production` → critical). Unknown action types default to HIGH (fail-safe). Hot-reloadable. Leaves door open for SecOps override in M7. Future strategies: SecOps-assigned, fixed-per-type.
> - **D20 — Context Serialization:** Pydantic JSON via persistence backend. `ParkedContext` model with metadata columns (`execution_id`, `agent_id`, `task_id`, `parked_at`) + `context_json` blob. `ParkedContextRepository` protocol via existing `PersistenceBackend` (§7.6). Conversation stored **verbatim** — summarization is a context window management concern at resume time, not a persistence concern.
> - **D21 — Resume Injection:** Tool result injection. Approval requests modeled as tool calls (`request_human_approval`). Approval decision returned as `ToolResult` — semantically correct (approval IS the tool's return value). LLM conversation protocol requires a tool result after a tool call. Fallback: system message injection for engine-initiated parking (exception path).

---

## 13. Human Interaction Layer

### 13.1 Architecture: API-First

The REST/WebSocket API is the **primary interface** for all consumers. The Web UI and any future CLI tool are thin clients that call the API — they contain no business logic.

```text
┌─────────────────────────────────────────────┐
│               AI Company Engine              │
│  (Core Logic, Agent Orchestration, Tasks)    │
└──────────────────┬──────────────────────────┘
                   │
          ┌────────▼────────┐
          │   REST/WS API    │  ← primary interface
          │   (Litestar)     │
          └───┬─────────┬───┘
              │         │
      ┌───────▼──┐  ┌───▼────────┐
      │  Web UI   │  │  CLI Tool   │
      │ (Future)  │  │  (Future)   │
      └──────────┘  └────────────┘
```

> **CLI Tool (Future):** If needed, a thin CLI utility wrapping the REST API with terminal formatting (Typer + Rich or similar). Not a priority — the API is fully self-sufficient. To be determined whether a dedicated CLI is warranted or whether `curl`/`httpie` and the interactive Scalar docs at `/docs/api` suffice.

### 13.2 API Surface

```text
/api/v1/
  ├── /health           # Health check, readiness
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
# templates/startup.yaml (simplified — real templates also declare
# variables, departments, min_agents/max_agents, and tags)
template:
  name: "Tech Startup"
  description: "Small team for building MVPs and prototypes"
  version: "1.0"

  company:
    type: "startup"
    budget_monthly: "{{ budget | default(50.00) }}"
    autonomy: 0.5

  agents:
    - role: "CEO"
      name: "{{ ceo_name | auto }}"
      model: "large"
      personality_preset: "visionary_leader"

    - role: "Full-Stack Developer"
      merge_id: "fullstack-senior"
      name: "{{ dev1_name | auto }}"
      level: "senior"
      model: "medium"
      personality_preset: "pragmatic_builder"

    - role: "Full-Stack Developer"
      merge_id: "fullstack-mid"
      name: "{{ dev2_name | auto }}"
      level: "mid"
      model: "small"
      personality_preset: "eager_learner"

    - role: "Product Manager"
      name: "{{ pm_name | auto }}"
      model: "medium"
      personality_preset: "strategic_planner"

  workflow: "agile_kanban"
  communication: "hybrid"

  workflow_handoffs:
    - from_department: "engineering"
      to_department: "qa"
      trigger: "pr_ready"

  escalation_paths:
    - from_department: "engineering"
      to_department: "security"
      condition: "vulnerability_found"
```

**Template Inheritance** — Templates can extend other templates using `extends`:

```yaml
template:
  name: "Extended Startup"
  extends: "startup"         # inherits all agents, departments, config
  agents:
    - role: "QA Engineer"    # appended to parent agents
      level: "mid"
    - role: "Full-Stack Developer"
      merge_id: "fullstack-mid"
      department: "engineering"
      _remove: true          # removes matching parent agent by key
```

Inheritance resolves parent→child chains up to 10 levels deep. Merge semantics:
- **Scalars** (`company_name`, `company_type`): child wins if present.
- **`config`** dict: deep-merged (child keys override parent).
- **`agents`** list: merged by `(role, department, merge_id)` key. When `merge_id` is omitted, it defaults to an empty string, making the key `(role, department, "")`. Child can override, append, or remove (`_remove: true`) parent agents.
- **`departments`** list: merged by name (case-insensitive). Child dept replaces parent entirely.
- **`workflow_handoffs`**, **`escalation_paths`**: child replaces entirely if present.

Circular inheritance is detected via chain tracking and raises `TemplateInheritanceError`.

### 14.2 Company Builder (Future)

> **Deferred.** The template system (§14.1) already supports creating companies from YAML configs. An interactive wizard is a nice-to-have after the REST API exists — it could be a thin CLI utility or a web form that POSTs to `/api/v1/company`. To be determined.

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

### 15.2 Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| **Language** | Python 3.14+ | Best AI/ML ecosystem, all major frameworks use it, LiteLLM/MCP and memory layer candidates all Python-native. PEP 649 native lazy annotations, PEP 758 except syntax. |
| **API Framework** | Litestar | Async-native, built-in channels (pub/sub WebSocket), auto OpenAPI 3.1 docs, class-based controllers, native route guards, built-in rate limiting / CSRF / compression middleware, explicit DI, Pydantic v2 support via plugin. Chosen over FastAPI — see §15.4 |
| **LLM Abstraction** | LiteLLM | 100+ providers, unified API, built-in cost tracking, retries/fallbacks |
| **Agent Memory** | Mem0 (Qdrant + SQLite) → custom (Neo4j + Qdrant) | Mem0 in-process as initial backend behind pluggable `MemoryBackend` protocol ([ADR-001](docs/decisions/ADR-001-memory-layer.md)). Qdrant embedded + SQLite for persistence. Custom stack (Neo4j + Qdrant external) as future upgrade. Config-driven backend selection |
| **Message Bus** | Internal (async queues) → Redis | Start with Python asyncio queues, upgrade to Redis for multi-process/distributed |
| **Task Queue** | Internal → Celery/Redis | Start simple, scale with Celery when needed |
| **Database** | SQLite (aiosqlite) → PostgreSQL / MariaDB | Pluggable `PersistenceBackend` protocol (§7.6). SQLite ships first via aiosqlite async driver. PostgreSQL, MariaDB as future backends — swap via config, no app code changes |
| **Web UI** | Vue 3 + Vite | Modern, fast, good ecosystem. Simpler than React for dashboards |
| **Real-time** | WebSocket (Litestar channels plugin) | Built-in pub/sub broadcasting, per-channel history, backpressure management. Real-time agent activity, task updates, chat feed |
| **Containerization** | Docker + Docker Compose | Production container packaging: Chainguard Python distroless runtime (non-root UID 65532, CIS Docker Benchmark v1.6.0 hardened, minimal attack surface, continuously scanned in CI), `nginxinc/nginx-unprivileged` web tier, GHCR registry, cosign image signing, Trivy + Grype vulnerability scanning, SBOM + SLSA provenance. Also used for isolated code execution sandboxing |
| **Docker API** | aiodocker | Async-native Docker API client for `DockerSandbox` backend |
| **Tool Integration** | MCP SDK (`mcp`) | Industry standard for LLM-to-tool integration |
| **Agent Comms** | A2A Protocol compatible | Future-proof inter-agent communication |
| **Config Format** | YAML + Pydantic validation | Human-readable config with strict validation |
| **CLI** | TBD (future, if needed) | Thin wrapper around the REST API for terminal use. May not be needed — interactive Scalar docs at `/docs/api` and `curl`/`httpie` may suffice |

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
│       │   ├── approval.py         # ApprovalItem domain model (approval queue)
│       │   ├── enums.py            # Core enumerations
│       │   ├── task.py             # Task model & state machine
│       │   ├── task_transitions.py # Task state transitions
│       │   ├── project.py          # Project management
│       │   ├── artifact.py         # Produced work items
│       │   ├── role.py             # Role model
│       │   ├── role_catalog.py     # Role catalog
│       │   ├── personality.py     # Personality compatibility scoring
│       │   └── resilience_config.py # RetryConfig, RateLimiterConfig (shared by config.schema + providers.resilience)
│       ├── engine/                  # Agent orchestration, execution loops, parallel execution, task decomposition, routing, task assignment, task lifecycle, recovery, shutdown, workspace isolation, coordination error classification, and prompt policy validation
│       │   ├── errors.py           # Engine error hierarchy
│       │   ├── prompt.py           # System prompt builder
│       │   ├── prompt_template.py  # System prompt Jinja2 templates
│       │   ├── task_execution.py   # TaskExecution + StatusTransition
│       │   ├── context.py          # AgentContext + AgentContextSnapshot
│       │   ├── loop_protocol.py    # ExecutionLoop protocol + result models
│       │   ├── metrics.py          # TaskCompletionMetrics proxy overhead model
│       │   ├── policy_validation.py # Org policy quality heuristics (non-inferable principle)
│       │   ├── react_loop.py       # ReAct loop implementation
│       │   ├── plan_models.py      # Plan step, plan, and plan-execute config models
│       │   ├── plan_execute_loop.py # Plan-and-Execute loop implementation
│       │   ├── plan_parsing.py     # Plan extraction from LLM responses (JSON + text fallback)
│       │   ├── loop_helpers.py     # Shared stateless helpers for all loop implementations
│       │   ├── recovery.py         # Crash recovery strategies (RecoveryStrategy protocol)
│       │   ├── cost_recording.py   # Per-turn cost recording helpers
│       │   ├── run_result.py       # AgentRunResult outcome model
│       │   ├── _validation.py      # Input validation helpers for AgentEngine
│       │   ├── agent_engine.py     # Agent execution engine
│       │   ├── parallel.py         # Parallel agent executor (TaskGroup + Semaphore)
│       │   ├── parallel_models.py  # AgentAssignment, ParallelExecutionGroup, AgentOutcome, ParallelExecutionResult, ParallelProgress
│       │   ├── resource_lock.py    # ResourceLock protocol + InMemoryResourceLock
│       │   ├── shutdown.py        # Graceful shutdown strategy & manager
│       │   ├── classification/      # Coordination error taxonomy classification (§10.5)
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── models.py      # ErrorSeverity, ErrorFinding, ClassificationResult
│       │   │   ├── detectors.py   # Per-category detection heuristics
│       │   │   └── pipeline.py    # classify_execution_errors orchestrator
│       │   ├── assignment/          # Task assignment subsystem
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── models.py      # AssignmentRequest, AssignmentResult, AssignmentCandidate, AgentWorkload
│       │   │   ├── protocol.py    # TaskAssignmentStrategy protocol
│       │   │   ├── service.py     # TaskAssignmentService (orchestrates strategy + validation)
│       │   │   ├── registry.py    # STRATEGY_MAP + build_strategy_map factory
│       │   │   └── strategies.py  # All 6 strategy implementations
│       │   ├── decomposition/      # Task decomposition subsystem
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── classifier.py  # TaskStructureClassifier (sequential/parallel/mixed)
│       │   │   ├── dag.py         # DependencyGraph (validation, topo sort, parallel groups)
│       │   │   ├── llm.py         # LlmDecompositionStrategy (LLM-based decomposition with tool calling)
│       │   │   ├── llm_prompt.py  # Prompt building and response parsing for LLM decomposition
│       │   │   ├── manual.py      # ManualDecompositionStrategy
│       │   │   ├── models.py      # SubtaskDefinition, DecompositionPlan, DecompositionResult, SubtaskStatusRollup, DecompositionContext
│       │   │   ├── protocol.py    # DecompositionStrategy protocol
│       │   │   ├── rollup.py      # StatusRollup (compute subtask status aggregation)
│       │   │   └── service.py     # DecompositionService (orchestrates strategy + classifier + DAG)
│       │   ├── workspace/          # Workspace isolation subsystem (§6.8)
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── config.py      # PlannerWorktreesConfig, WorkspaceIsolationConfig
│       │   │   ├── git_worktree.py # PlannerWorktreeStrategy (git worktree backend)
│       │   │   ├── merge.py       # MergeOrchestrator (sequential merge with conflict escalation)
│       │   │   ├── models.py      # Workspace, WorkspaceRequest, MergeResult, MergeConflict, WorkspaceGroupResult
│       │   │   ├── protocol.py    # WorkspaceIsolationStrategy protocol
│       │   │   └── service.py     # WorkspaceIsolationService (lifecycle orchestrator)
│       │   ├── routing/            # Task routing subsystem
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── models.py      # RoutingCandidate, RoutingDecision, RoutingResult, AutoTopologyConfig
│       │   │   ├── scorer.py      # AgentTaskScorer (skill/role/seniority matching)
│       │   │   ├── service.py     # TaskRoutingService (routes subtasks to agents)
│       │   │   └── topology_selector.py # TopologySelector (auto coordination topology)
│       ├── hr/                      # HR engine: hiring, firing, onboarding, offboarding, agent registry, performance tracking
│       │   ├── __init__.py         # Package exports
│       │   ├── enums.py            # HR enumerations (HiringRequestStatus, FiringReason, OnboardingStep, LifecycleEventType, TrendDirection, PromotionDirection)
│       │   ├── errors.py           # HR error hierarchy
│       │   ├── models.py           # CandidateCard, HiringRequest, FiringRequest, OnboardingChecklist, OffboardingRecord, AgentLifecycleEvent
│       │   ├── registry.py         # AgentRegistryService (agent lifecycle registry)
│       │   ├── hiring_service.py   # HiringService (request → generate candidate → approval → instantiate)
│       │   ├── onboarding_service.py # OnboardingService (checklist management)
│       │   ├── offboarding_service.py # OffboardingService (reassign → archive → notify → terminate)
│       │   ├── archival_protocol.py  # MemoryArchivalStrategy protocol
│       │   ├── full_snapshot_strategy.py # FullSnapshotArchivalStrategy
│       │   ├── reassignment_protocol.py # TaskReassignmentStrategy protocol
│       │   ├── queue_return_strategy.py # QueueReturnReassignmentStrategy
│       │   ├── persistence_protocol.py # HR-specific repository protocols
│       │   └── performance/         # Performance tracking subsystem
│       │       ├── __init__.py     # Package exports
│       │       ├── models.py       # TaskMetricRecord, CollaborationMetricRecord, WindowMetrics, TrendResult, etc.
│       │       ├── config.py       # PerformanceConfig
│       │       ├── tracker.py      # PerformanceTracker service
│       │       ├── quality_protocol.py # QualityScorer protocol
│       │       ├── ci_quality_strategy.py # CiQualityScorer (CI-based quality scoring)
│       │       ├── collaboration_protocol.py # CollaborationScorer protocol
│       │       ├── behavioral_collaboration_strategy.py # BehavioralCollaborationScorer
│       │       ├── trend_protocol.py # TrendDetector protocol
│       │       ├── theil_sen_strategy.py # TheilSenTrendDetector (robust trend detection)
│       │       ├── window_protocol.py # WindowAggregator protocol
│       │       └── multi_window_strategy.py # MultiWindowAggregator (multi-window rolling metrics)
│       │   └── promotion/         # Promotion/demotion subsystem (D14)
│       │       ├── config.py      # PromotionConfig, PromotionCriteriaConfig, PromotionApprovalConfig, ModelMappingConfig
│       │       ├── models.py      # CriterionResult, PromotionEvaluation, PromotionApprovalDecision, PromotionRecord, PromotionRequest
│       │       ├── criteria_protocol.py    # PromotionCriteriaStrategy protocol
│       │       ├── approval_protocol.py    # PromotionApprovalStrategy protocol
│       │       ├── model_mapping_protocol.py # ModelMappingStrategy protocol
│       │       ├── threshold_evaluator.py  # ThresholdEvaluator (criteria evaluation)
│       │       ├── seniority_approval_strategy.py # SeniorityApprovalStrategy (approval decisions)
│       │       ├── seniority_model_mapping.py # SeniorityModelMapping (model resolution)
│       │       └── service.py     # PromotionService orchestrator (evaluate, request, apply)
│       ├── communication/           # Inter-agent communication
│       │   ├── bus_memory.py       # InMemoryMessageBus implementation
│       │   ├── bus_protocol.py     # MessageBus protocol interface
│       │   ├── channel.py          # Channel model
│       │   ├── config.py           # Communication config
│       │   ├── conflict_resolution/ # Conflict resolution subsystem (§5.6)
│       │   │   ├── __init__.py   # Package exports
│       │   │   ├── _helpers.py   # Shared utility (find_losers, pick_highest_seniority)
│       │   │   ├── authority_strategy.py # AuthorityResolver (Strategy 1)
│       │   │   ├── config.py     # ConflictResolutionConfig, DebateConfig, HybridConfig
│       │   │   ├── debate_strategy.py   # DebateResolver (Strategy 2)
│       │   │   ├── human_strategy.py    # HumanEscalationResolver (Strategy 3)
│       │   │   ├── hybrid_strategy.py   # HybridResolver (Strategy 4)
│       │   │   ├── models.py     # Conflict, ConflictPosition, ConflictResolution, DissentRecord
│       │   │   ├── protocol.py   # ConflictResolver, JudgeEvaluator protocols
│       │   │   └── service.py    # ConflictResolutionService (orchestrator)
│       │   ├── delegation/         # Hierarchical delegation subsystem
│       │   │   ├── __init__.py   # Package exports
│       │   │   ├── authority.py   # AuthorityValidator + AuthorityCheckResult
│       │   │   ├── hierarchy.py   # HierarchyResolver (org hierarchy from Company)
│       │   │   ├── models.py      # DelegationRequest, DelegationResult, DelegationRecord
│       │   │   └── service.py     # DelegationService (orchestrates delegation flow)
│       │   ├── dispatcher.py       # MessageDispatcher + DispatchResult
│       │   ├── enums.py            # Communication enums
│       │   ├── errors.py           # Communication + delegation error hierarchy
│       │   ├── handler.py          # MessageHandler protocol, FunctionHandler, HandlerRegistration
│       │   ├── loop_prevention/    # Delegation loop prevention mechanisms
│       │   │   ├── __init__.py   # Package exports
│       │   │   ├── _pair_key.py   # Canonical agent-pair key utility
│       │   │   ├── ancestry.py    # Ancestry cycle detection (pure function)
│       │   │   ├── circuit_breaker.py # DelegationCircuitBreaker, CircuitBreakerState
│       │   │   ├── dedup.py       # DelegationDeduplicator (time-windowed)
│       │   │   ├── depth.py       # Max delegation depth check (pure function)
│       │   │   ├── guard.py       # DelegationGuard (orchestrates all mechanisms)
│       │   │   ├── models.py      # GuardCheckOutcome
│       │   │   └── rate_limit.py  # DelegationRateLimiter (per-pair)
│       │   ├── message.py          # Message model
│       │   ├── meeting/             # Meeting protocol subsystem
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── _parsing.py   # Shared helpers for parsing decisions and action items
│       │   │   ├── _prompts.py    # LLM prompt templates for meeting phases
│       │   │   ├── _token_tracker.py # TokenTracker for duration_tokens enforcement
│       │   │   ├── config.py      # MeetingProtocolConfig, protocol-specific config models
│       │   │   ├── enums.py       # MeetingProtocolType, MeetingPhase enums
│       │   │   ├── errors.py      # Meeting error hierarchy
│       │   │   ├── models.py      # MeetingRecord, MeetingAgendaItem, ActionItem, etc.
│       │   │   ├── orchestrator.py # MeetingOrchestrator (runs meetings end-to-end)
│       │   │   ├── position_papers.py # PositionPapersProtocol implementation
│       │   │   ├── protocol.py    # MeetingProtocol protocol interface
│       │   │   ├── round_robin.py # RoundRobinProtocol implementation
│       │   │   └── structured_phases.py # StructuredPhasesProtocol implementation
│       │   ├── messenger.py        # AgentMessenger per-agent facade
│       │   └── subscription.py     # Subscription + DeliveryEnvelope models
│       ├── memory/                  # Agent memory system — protocols, models, config, factory, retrieval pipeline (ranking, injection, context formatting, non-inferable filtering) (M5)
│       │   ├── __init__.py         # Re-exports
│       │   ├── capabilities.py     # MemoryCapabilities protocol
│       │   ├── config.py           # CompanyMemoryConfig, MemoryStorageConfig, MemoryOptionsConfig
│       │   ├── errors.py           # Memory error hierarchy (MemoryError and subclasses)
│       │   ├── factory.py          # create_memory_backend() factory
│       │   ├── formatter.py        # format_memory_context() — ranked memories to ChatMessage(s)
│       │   ├── injection.py        # MemoryInjectionStrategy protocol, InjectionStrategy enum, TokenEstimator
│       │   ├── models.py           # MemoryEntry, MemoryMetadata, MemoryQuery, MemoryStoreRequest
│       │   ├── protocol.py         # MemoryBackend protocol
│       │   ├── ranking.py          # ScoredMemory model, rank_memories(), scoring functions
│       │   ├── retrieval_config.py # MemoryRetrievalConfig (weights, thresholds, strategy selection)
│       │   ├── filter.py            # MemoryFilterStrategy protocol, TagBasedMemoryFilter, PassthroughMemoryFilter
│       │   ├── retriever.py        # ContextInjectionStrategy (full retrieval → rank → format pipeline)
│       │   ├── store_guard.py      # Advisory non-inferable tag enforcement at store boundary
│       │   ├── shared.py           # SharedKnowledgeStore protocol
│       │   ├── consolidation/    # Memory consolidation — strategies, retention, archival
│       │   │   ├── __init__.py
│       │   │   ├── archival.py   # ArchivalStore protocol
│       │   │   ├── config.py     # ConsolidationConfig, ArchivalConfig, RetentionConfig
│       │   │   ├── models.py     # ConsolidationResult, ArchivalEntry, RetentionRule
│       │   │   ├── retention.py  # RetentionEnforcer
│       │   │   ├── service.py    # MemoryConsolidationService
│       │   │   ├── simple_strategy.py # SimpleConsolidationStrategy
│       │   │   └── strategy.py   # ConsolidationStrategy protocol
│       │   └── org/              # Shared organizational memory (§7.4)
│       │       ├── __init__.py
│       │       ├── access_control.py # Write access control
│       │       ├── config.py     # OrgMemoryConfig
│       │       ├── errors.py     # OrgMemory error hierarchy
│       │       ├── factory.py    # create_org_memory_backend()
│       │       ├── hybrid_backend.py # HybridPromptRetrievalBackend
│       │       ├── models.py     # OrgFact, OrgFactAuthor, OrgMemoryQuery
│       │       ├── protocol.py   # OrgMemoryBackend protocol
│       │       └── store.py      # OrgFactStore protocol, SQLiteOrgFactStore
│       ├── persistence/             # Operational data persistence (§7.6)
│       │   ├── __init__.py         # Package exports
│       │   ├── protocol.py         # PersistenceBackend protocol (M5)
│       │   ├── repositories.py     # Repository protocols: TaskRepository, CostRecordRepository, MessageRepository, ParkedContextRepository, AuditRepository
│       │   ├── config.py           # PersistenceConfig model (M5)
│       │   ├── errors.py           # Persistence error hierarchy (M5)
│       │   ├── factory.py          # create_backend() factory (M5)
│       │   └── sqlite/             # SQLite backend (M5, initial)
│       │       ├── __init__.py    # Package exports
│       │       ├── backend.py     # SQLitePersistenceBackend
│       │       ├── repositories.py # SQLite repository implementations
│       │       ├── hr_repositories.py # SQLite HR repositories (LifecycleEvent, TaskMetricRecord, CollaborationMetricRecord)
│       │       ├── parked_context_repo.py # SQLiteParkedContextRepository (park/resume serialized agent state)
│       │       ├── audit_repository.py # SQLiteAuditRepository (append-only audit entry persistence)
│       │       └── migrations.py  # Schema migrations (user_version pragma)
│       ├── observability/           # Structured logging & correlation
│       │   ├── __init__.py         # get_logger() entry point
│       │   ├── _logger.py          # Logger configuration
│       │   ├── config.py           # Observability config
│       │   ├── correlation.py      # Correlation ID tracking
│       │   ├── enums.py            # Log-related enums
│       │   ├── events/             # Per-domain event constants
│       │   │   ├── __init__.py    # Package marker with usage docs; no re-exports
│       │   │   ├── api.py            # API_* event constants
│       │   │   ├── autonomy.py    # AUTONOMY_* constants
│       │   │   ├── budget.py      # BUDGET_* constants
│       │   │   ├── cfo.py         # CFO_* constants
│       │   │   ├── classification.py # CLASSIFICATION_* constants
│       │   │   ├── consolidation.py # CONSOLIDATION_* and RETENTION_* constants
│       │   │   ├── company.py      # COMPANY_* constants
│       │   │   ├── communication.py # COMM_* constants
│       │   │   ├── conflict.py    # CONFLICT_* constants
│       │   │   ├── config.py      # CONFIG_* constants
│       │   │   ├── delegation.py  # DELEGATION_* constants
│       │   │   ├── correlation.py # CORRELATION_* constants
│       │   │   ├── decomposition.py # DECOMPOSITION_* constants
│       │   │   ├── execution.py   # EXECUTION_* constants
│       │   │   ├── git.py         # GIT_* constants
│       │   │   ├── hr.py         # HR_* constants
│       │   │   ├── meeting.py    # MEETING_* constants
│       │   │   ├── memory.py     # MEMORY_* constants
│       │   │   ├── org_memory.py # ORG_MEMORY_* constants
│       │   │   ├── parallel.py    # PARALLEL_* constants
│       │   │   ├── performance.py # PERF_* constants
│       │   │   ├── persistence.py # PERSISTENCE_* constants
│       │   │   ├── personality.py # PERSONALITY_* constants
│       │   │   ├── prompt.py      # PROMPT_* constants
│       │   │   ├── quota.py       # QUOTA_* event constants
│       │   │   ├── provider.py    # PROVIDER_* constants
│       │   │   ├── role.py        # ROLE_* constants
│       │   │   ├── routing.py     # ROUTING_* constants
│       │   │   ├── sandbox.py     # SANDBOX_* constants
│       │   │   ├── security.py    # SECURITY_* constants
│       │   │   ├── task.py        # TASK_* constants
│       │   │   ├── task_assignment.py # TASK_ASSIGNMENT_* constants
│       │   │   ├── task_routing.py # TASK_ROUTING_* constants
│       │   │   ├── template.py    # TEMPLATE_* constants
│       │   │   ├── timeout.py     # TIMEOUT_* constants
│       │   │   ├── tool.py        # TOOL_* constants
│       │   │   ├── workspace.py   # WORKSPACE_* constants
│       │   │   ├── code_runner.py # CODE_RUNNER_* constants
│       │   │   ├── docker.py      # DOCKER_* constants
│       │   │   ├── mcp.py         # MCP_* constants
│       │   │   ├── trust.py       # Trust event constants
│       │   │   └── promotion.py   # Promotion event constants
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
│       │   ├── routing/            # Model routing (5 strategies)
│       │   │   ├── _strategy_helpers.py  # Shared routing helper functions
│       │   │   ├── errors.py      # Routing errors
│       │   │   ├── models.py      # Routing models (candidates, results)
│       │   │   ├── resolver.py    # Model resolver
│       │   │   ├── router.py      # Router orchestrator
│       │   │   └── strategies.py  # Routing strategies
│       │   └── resilience/         # Resilience patterns
│       │       ├── errors.py      # RetryExhaustedError
│       │       ├── rate_limiter.py # Token bucket rate limiter
│       │       └── retry.py       # RetryHandler with backoff
│       ├── tools/                   # Tool/capability system
│       │   ├── base.py             # BaseTool ABC, ToolExecutionResult
│       │   ├── registry.py         # Immutable tool registry (MappingProxyType)
│       │   ├── invoker.py          # Tool invocation (concurrent via TaskGroup)
│       │   ├── permissions.py      # ToolPermissionChecker (access-level gating)
│       │   ├── errors.py           # Tool error hierarchy (incl. ToolPermissionDeniedError)
│       │   ├── examples/           # Example tool implementations
│       │   │   ├── __init__.py    # Package exports
│       │   │   └── echo.py        # Echo tool (for testing)
│       │   ├── file_system/        # Built-in file system tools
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── _base_fs_tool.py  # BaseFileSystemTool ABC
│       │   │   ├── _path_validator.py # Workspace path validation
│       │   │   ├── delete_file.py # DeleteFileTool
│       │   │   ├── edit_file.py   # EditFileTool
│       │   │   ├── list_directory.py # ListDirectoryTool
│       │   │   ├── read_file.py   # ReadFileTool
│       │   │   └── write_file.py  # WriteFileTool
│       │   ├── _git_base.py        # Base class for git tools (workspace, subprocess, sandbox integration)
│       │   ├── _process_cleanup.py  # Subprocess transport cleanup utility (Windows ResourceWarning prevention)
│       │   ├── git_tools.py        # Git operations — 6 built-in tools (sandbox-aware)
│       │   ├── code_runner.py      # Code execution tool
│       │   ├── web_tools.py        # HTTP, search (M7)
│       │   ├── sandbox/             # Sandbox backends subpackage
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── config.py      # Subprocess sandbox configuration
│       │   │   ├── docker_config.py # Docker sandbox configuration
│       │   │   ├── docker_sandbox.py # DockerSandbox backend (aiodocker)
│       │   │   ├── errors.py      # Sandbox error hierarchy
│       │   │   ├── protocol.py    # SandboxBackend protocol
│       │   │   ├── result.py      # SandboxResult model
│       │   │   ├── sandboxing_config.py # Top-level sandboxing config
│       │   │   └── subprocess_sandbox.py # SubprocessSandbox backend
│       │   └── mcp/                # MCP bridge subpackage
│       │       ├── __init__.py    # Package exports
│       │       ├── bridge_tool.py # MCPBridgeTool (BaseTool integration)
│       │       ├── cache.py       # MCP result cache (TTL + LRU)
│       │       ├── client.py      # MCP client wrapper
│       │       ├── config.py      # MCP server/bridge config models
│       │       ├── errors.py      # MCP error hierarchy
│       │       ├── factory.py     # MCPToolFactory (parallel connect)
│       │       ├── models.py      # MCP domain models
│       │       └── result_mapper.py # MCP result → ToolExecutionResult mapping
│       ├── security/                # Security & approval
│       │   ├── action_type_mapping.py # Default ToolCategory → ActionType mapping
│       │   ├── action_types.py     # ActionTypeCategory registry and validation
│       │   ├── audit.py            # Append-only AuditLog with configurable eviction
│       │   ├── config.py           # SecurityConfig, SecurityPolicyRule, RuleEngineConfig, OutputScanPolicyType
│       │   ├── models.py           # SecurityVerdict, SecurityContext, AuditEntry, OutputScanResult
│       │   ├── output_scan_policy.py # Output scan response policies (redact/withhold/log-only/autonomy-tiered)
│       │   ├── output_scan_policy_factory.py # build_output_scan_policy() factory
│       │   ├── output_scanner.py   # Post-tool output scanning (regex-based redaction)
│       │   ├── protocol.py         # SecurityInterceptionStrategy protocol
│       │   ├── service.py          # SecOpsService — meta-agent coordinating security
│       │   ├── autonomy/           # Autonomy levels, presets, resolver, change strategy (§12.2)
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── models.py      # AutonomyLevel enum, AutonomyPreset, AutonomyConfig, AutonomyChangeEvent
│       │   │   ├── protocol.py    # AutonomyChangeStrategy protocol
│       │   │   ├── change_strategy.py # Rule-based auto-downgrade + human-only promotion strategy
│       │   │   └── resolver.py    # AutonomyResolver (agent → department → company chain)
│       │   ├── timeout/            # Approval timeout policies, park/resume, risk tier classifier (§12.4)
│       │   │   ├── __init__.py    # Package exports
│       │   │   ├── config.py      # TimeoutPolicyConfig
│       │   │   ├── factory.py     # build_timeout_policy() factory
│       │   │   ├── models.py      # TimeoutDecision, RiskTier
│       │   │   ├── park_service.py # ParkResumeService (park/resume blocked tasks)
│       │   │   ├── parked_context.py # ParkedContext model (serialized agent state)
│       │   │   ├── policies.py    # WaitForeverPolicy, AutoDenyPolicy, TieredPolicy, EscalationChainPolicy
│       │   │   ├── protocol.py    # TimeoutPolicy protocol
│       │   │   ├── risk_tier_classifier.py # RiskTierClassifier (ActionType → RiskTier)
│       │   │   └── timeout_checker.py # TimeoutChecker (polls pending approvals)
│       │   └── rules/              # Rule engine and detectors
│       │       ├── engine.py       # RuleEngine (soft-allow + hard-deny, fail-closed)
│       │       ├── protocol.py     # SecurityRule protocol
│       │       ├── policy_validator.py        # Policy list validation rule (hard-deny/auto-approve)
│       │       ├── risk_classifier.py         # RiskClassifier (ActionType → ApprovalRiskLevel)
│       │       ├── credential_detector.py     # Credential/secret pattern detection (API keys, tokens)
│       │       ├── data_leak_detector.py      # Data leak detection (PII, sensitive file paths)
│       │       ├── destructive_op_detector.py # Destructive operation detection (rm -rf, DROP TABLE)
│       │       ├── path_traversal_detector.py # Path traversal attack detection (../, null bytes)
│       │       └── _utils.py       # walk_string_values utility (recursive argument scanning)
│       │   └── trust/              # Progressive trust subsystem (§11.3)
│       │       ├── config.py       # TrustConfig, strategy-specific sub-configs
│       │       ├── enums.py        # TrustStrategyType, TrustChangeReason
│       │       ├── errors.py       # TrustEvaluationError
│       │       ├── models.py       # TrustState, TrustEvaluationResult, TrustChangeRecord
│       │       ├── protocol.py     # TrustStrategy protocol
│       │       ├── service.py      # TrustService orchestrator (state, evaluation, decay, approval)
│       │       ├── disabled_strategy.py    # DisabledTrustStrategy (passthrough)
│       │       ├── weighted_strategy.py    # WeightedTrustStrategy (weighted score → thresholds)
│       │       ├── per_category_strategy.py # PerCategoryTrustStrategy (per-tool-category tracks)
│       │       └── milestone_strategy.py   # MilestoneTrustStrategy (milestone gates + decay)
│       ├── budget/                  # Cost management
│       │   ├── _optimizer_helpers.py # CostOptimizer shared helper functions
│       │   ├── config.py           # Budget configuration models
│       │   ├── cost_record.py      # CostRecord model (frozen)
│       │   ├── cost_tiers.py      # Cost tier definitions, classification, and built-in tiers
│       │   ├── call_category.py   # LLM call category enums (productive, coordination, system)
│       │   ├── category_analytics.py # Per-category cost breakdown + orchestration ratio
│       │   ├── coordination_config.py # Coordination metrics config models
│       │   ├── coordination_metrics.py # Five coordination metric models + computation
│       │   ├── tracker.py          # CostTracker service (records + queries)
│       │   ├── spending_summary.py # _SpendingTotals base + spending summary models
│       │   ├── hierarchy.py        # BudgetHierarchy, BudgetConfig
│       │   ├── enums.py            # Budget-related enums
│       │   ├── billing.py          # Billing period computation utilities
│       │   ├── enforcer.py         # BudgetEnforcer service (pre-flight, in-flight, auto-downgrade)
│       │   ├── errors.py           # BudgetExhaustedError, DailyLimitExceededError, QuotaExhaustedError
│       │   ├── optimizer.py        # CostOptimizer service — anomaly detection, efficiency analysis, downgrade recommendations, approval decisions (M5)
│       │   ├── optimizer_models.py # CostOptimizer domain models — anomaly, efficiency, downgrade, approval, config (M5)
│       │   ├── quota.py            # Quota/subscription models, degradation config, quota snapshots
│       │   ├── quota_tracker.py    # QuotaTracker service: per-provider request/token quota enforcement
│       │   └── reports.py          # Spending reports (M5)
│       ├── api/                     # REST + WebSocket API (M6)
│       │   ├── app.py              # Litestar application factory, lifecycle hooks
│       │   ├── approval_store.py   # In-memory approval queue storage
│       │   ├── bus_bridge.py       # Message-bus → WebSocket bridge
│       │   ├── channels.py         # WebSocket channel definitions
│       │   ├── config.py           # API configuration models (ServerConfig, CorsConfig)
│       │   ├── controllers/        # 14 class-based controllers + 1 WebSocket handler (15 route modules)
│       │   ├── dto.py              # Request/response DTOs and envelopes
│       │   ├── errors.py           # API error hierarchy (ApiError, NotFoundError, etc.)
│       │   ├── exception_handlers.py # Litestar exception handler registration
│       │   ├── guards.py           # Route guards — read/write access (stub auth, M7 real auth)
│       │   ├── middleware.py       # Request logging middleware
│       │   ├── pagination.py       # Cursor-free offset/limit pagination
│       │   ├── server.py           # Uvicorn server runner
│       │   ├── state.py            # Typed AppState container with service access
│       │   └── ws_models.py        # WebSocket event models (WsEvent, WsEventType)
│       ├── cli/                     # CLI interface (future, if needed)
│       │   ├── __init__.py
│       │   └── commands/
│       │       └── __init__.py
│       └── templates/               # Company templates
│           ├── schema.py           # Template schema models
│           ├── loader.py           # Template loader
│           ├── renderer.py         # Template renderer
│           ├── merge.py            # Template config merging for inheritance
│           ├── presets.py          # Personality presets + auto-name generation
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
│   ├── decisions/
│   │   ├── ADR-001-memory-layer.md
│   │   └── ADR-002-design-decisions-batch-1.md
│   └── getting_started.md
├── docker/
│   ├── backend/
│   │   └── Dockerfile              # 3-stage: python:3.14-slim → chainguard/python-dev → chainguard/python (distroless)
│   ├── sandbox/
│   │   └── Dockerfile              # Code execution sandbox (Python + Node.js, non-root)
│   ├── web/
│   │   └── Dockerfile              # nginxinc/nginx-unprivileged (non-root)
│   ├── compose.yml                 # CIS-hardened orchestration
│   ├── compose.override.yml        # Local dev overrides (debug logging)
│   └── .env.example                # Environment variable reference
├── web/
│   ├── index.html                  # Placeholder dashboard with health check
│   └── nginx.conf                  # SPA routing + API/WebSocket proxy
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                  # Lint + type-check + test (parallel)
│   │   ├── docker.yml              # Build → scan → push → sign (GHCR)
│   │   ├── dependency-review.yml   # License allow-list on PRs
│   │   └── secret-scan.yml         # Gitleaks on push/PR + weekly
│   ├── actions/
│   │   └── setup-python-uv/        # Composite action: Python + uv install
│   ├── dependabot.yml              # uv + github-actions + docker updates
│   ├── CONTRIBUTING.md
│   └── SECURITY.md
├── .dockerignore                    # Consolidated Docker build context exclusions
├── .grype.yaml                      # Grype CVE ignore list (synced with .trivyignore.yaml)
├── .trivyignore.yaml                # Trivy CVE ignore list (structured YAML format)
├── DESIGN_SPEC.md                   # This document
├── README.md
├── pyproject.toml
└── CLAUDE.md
```

### 15.4 Key Design Decisions (Preliminary - Subject to Research)

| Decision | Choice | Alternatives Considered | Rationale |
|----------|--------|------------------------|-----------|
| Language | Python 3.14+ | TypeScript, Go, Rust | AI ecosystem, LiteLLM/MCP and memory layer candidates are Python-native, PEP 649 lazy annotations, PEP 758 except syntax |
| API | Litestar | FastAPI, Flask, Django, aiohttp | Built-in channels (pub/sub WebSocket), class-based controllers, native route guards, middleware (rate limiting, CSRF, compression), explicit DI. FastAPI considered but Litestar offers more batteries-included for less custom code — see rationale below |
| LLM Layer | LiteLLM | Direct APIs, OpenRouter only | 100+ providers, cost tracking, fallbacks, load balancing built-in |
| Memory | Mem0 (initial) → custom stack (future) + SQLite | Graphiti, Letta, Cognee, custom | Mem0 in-process as initial backend behind pluggable `MemoryBackend` protocol ([ADR-001](docs/decisions/ADR-001-memory-layer.md)). Custom stack (Neo4j + Qdrant) as future upgrade. Must support episodic, semantic, procedural memory types (§7.1–7.3). Org memory served via `OrgMemoryBackend` protocol (§7.4) |
| Message Bus | asyncio queues → Redis | Kafka, RabbitMQ, NATS | Start simple, Redis well-supported, Kafka overkill for local |
| Config | YAML + Pydantic | JSON, TOML, Python dicts | Human-friendly, strict validation, good IDE support |
| CLI | Deferred (TBD) | Typer, Click, argparse | Thin API wrapper if needed. Scalar interactive docs at `/docs/api` + `curl`/`httpie` may suffice |
| Web UI | Vue 3 | React, Svelte, HTMX | Simpler than React for dashboards |
| Persistence | Pluggable protocol + repository protocols | ORM (SQLAlchemy), raw SQL, hybrid | Same frozen Pydantic models in and out (no DTOs), async throughout, backend-swappable via config. Repository protocols decouple app code from storage engine. See §7.6 |
| Sandboxing | Layered: subprocess + Docker | Docker-only, subprocess-only, WASM | Risk-proportionate: fast subprocess for file/git, Docker isolation for code execution. Pluggable `SandboxBackend` protocol enables K8s migration later |
| Container Packaging | Chainguard distroless + GHCR | Alpine, Debian-slim, scratch, Docker Hub | Chainguard Python distroless: no shell/package-manager (minimal attack surface), non-root by default, continuously scanned in CI. GHCR over Docker Hub: tighter GitHub integration, no rate limits for public images, native OIDC token auth. cosign keyless signing for supply-chain integrity. Trivy + Grype dual scanning for comprehensive CVE coverage |

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
| **Parallel agent execution** | Adopted (M3) | `ParallelExecutor` coordinates concurrent `AgentEngine.run()` calls via `asyncio.TaskGroup` + optional `Semaphore` concurrency limit + `_run_guarded()` error isolation. `ResourceLock` protocol with `InMemoryResourceLock` for exclusive file-path claims. Progress tracking via `ProgressCallback`. Shutdown-aware via `ShutdownManager` task registration. Fail-fast mode cancels sibling tasks on first failure; all errors are surfaced via `ParallelExecutionResult` outcomes. | Follows the `ToolInvoker.invoke_all()` pattern (parallel tool execution above). Composition over inheritance — wraps `AgentEngine`. Structured concurrency with proper cancellation. See §6.3 Parallel Execution. |
| **Tool permission checking** | Adopted (M3) | `ToolPermissionChecker` enforces category-level gating based on `ToolAccessLevel` (sandboxed → restricted → standard → elevated, plus custom). Priority-based resolution: denied list → allowed list → level categories → deny. Case-insensitive name matching. `ToolInvoker` filters definitions for prompt and checks at invocation time. | Defense-in-depth: agents only see permitted tools in the LLM prompt, and invocations are re-checked at execution time. Explicit allow/deny lists provide per-agent overrides. See §11.1.1. |
| **Tool sandboxing** | Adopted (M3, incremental) | File system tools use in-process `PathValidator` for workspace-scoped path validation (symlink resolution + containment check). `BaseFileSystemTool` ABC provides shared `ToolCategory.FILE_SYSTEM` and `PathValidator` integration — all file system tools extend this base. `SandboxBackend` protocol with `SubprocessSandbox` implemented — git tools accept optional `SandboxBackend` injection and delegate subprocess management to it (env filtering, workspace enforcement, timeout + process-group kill). `DockerSandbox` planned for code_runner, terminal, web, and database tools. `K8sSandbox` planned for future container deployments. Config-driven per-category backend selection planned for engine wiring. | File system tools use defence-in-depth path validation; subprocess sandbox provides lightweight isolation for git tools; heavier Docker/K8s isolation reserved for higher-risk tool categories (code execution, network). See §11.1.2. |
| **Crash recovery** | Adopted (M3) | Pluggable `RecoveryStrategy` protocol. M3: `FailAndReassignStrategy` (catch at engine boundary, log snapshot, mark FAILED / eligible for reassignment). M4/M5: `CheckpointStrategy` (persist `AgentContext` per turn, resume from last checkpoint). | Immutable `model_copy` pattern makes checkpoint serialization trivial to add later. Fail-and-reassign is sufficient for short MVP tasks. See §6.6. |
| **Personality compatibility scoring** | Adopted (M3) | Weighted composite: 60% Big Five similarity (openness, conscientiousness, agreeableness, stress_response → 1−\|diff\|; extraversion → tent-function peaking at 0.3 diff), 20% collaboration alignment (ordinal adjacency: INDEPENDENT↔PAIR↔TEAM), 20% conflict approach (constructive pairs score 1.0, destructive pairs 0.2, mixed 0.4–0.6). `itertools.combinations` for team-level averaging. Result clamped to [0, 1]. | Covers behavioral diversity (extraversion complement), task alignment (conscientiousness similarity), and interpersonal friction (conflict approach). Weights are configurable module constants. |
| **Agent behavior testing** | Planned (M3) | Scripted `FakeProvider` for unit tests (deterministic turn sequences); behavioral outcome assertions for integration tests (task completed, tools called, cost within budget). | Leverages existing `FakeProvider` and `CompletionResponseFactory` fixtures. Precise engine testing without brittle response-matching at integration level. |
| **LLM call analytics** | Adopted (incremental) | M3: proxy metrics (`turns_per_task`, `tokens_per_task`) — adopted. M4 data models: call categorization (`productive`, `coordination`, `system`), category analytics, coordination metrics, orchestration ratio — adopted. M4 runtime collection pipeline and M5+ full analytics: planned. | Append-only, never blocks execution. Builds on existing `CostRecord` infrastructure. Detects orchestration overhead early. See §10.5. |
| **Cost tiers & quota tracking** | Adopted (M5) | Configurable `CostTierDefinition` definitions with merge/override semantics via `resolve_tiers(config: CostTiersConfig)`. `SubscriptionConfig` + `QuotaLimit` model per-provider subscription plans. `QuotaTracker` enforces per-provider request/token quotas with window-based rotation. `DegradationConfig` controls behavior when quotas are exhausted (default: `ALERT` — raise error; `FALLBACK` and `QUEUE` strategies defined but not yet implemented). | Enables cost classification without hardcoding vendor tiers. Quota tracking prevents surprise overages at the provider level. Window-based rotation aligns quota resets with billing periods. See §10.4. |
| **Shared org memory** | Adopted (M5) | `OrgMemoryBackend` protocol (pluggable) with `HybridPromptRetrievalBackend` (Backend 1). `OrgFactStore` protocol with `SQLiteOrgFactStore` for persistent fact storage. Seniority-based write access control via `CategoryWriteRule`. Core policies injected into system prompts; extended facts retrieved on demand via `OrgMemoryQuery`. `OrgFact` model with `OrgFactAuthor` provenance tracking. Config-driven via `OrgMemoryConfig`. | Pluggable backend mirrors `MemoryBackend` pattern. Hybrid prompt+retrieval balances always-available core policies with on-demand extended knowledge. Seniority-based access control prevents junior agents from overwriting organizational knowledge. See §7.4. |
| **Memory consolidation** | Adopted (M5) | `ConsolidationStrategy` protocol with `SimpleConsolidationStrategy` (deduplication + summarization). `RetentionEnforcer` for per-category age-based cleanup via `RetentionRule` policies. `ArchivalStore` protocol for cold storage before deletion. `MemoryConsolidationService` orchestrates retention → consolidation → max-memories enforcement pipeline. `ConsolidationResult` tracks statistics. Config-driven via `ConsolidationConfig` + `RetentionConfig` + `ArchivalConfig`. | Prevents unbounded memory growth. Pluggable strategy enables different consolidation approaches (simple dedup now, LLM-based summarization later). Retention + archival ensures compliance with data lifecycle policies. See §7.4. |
| **State coordination** | Planned (M4) | Centralized single-writer: `TaskEngine` owns all task/project mutations via `asyncio.Queue`. Agents submit requests, engine applies `model_copy(update=...)` sequentially and publishes snapshots. `version: int` field on state models for future optimistic concurrency if multi-process scaling is needed. | Prevents lost updates by design. Trivial in single-threaded asyncio (no locks). Perfect audit trail. Industry consensus: MetaGPT, CrewAI, AutoGen all use prevention-by-design, not conflict resolution. See §6.8 State Coordination table. |
| **Workspace isolation** | Adopted (M4 core) | Pluggable `WorkspaceIsolationStrategy` protocol. Default: planner + git worktrees. Each agent works in an isolated worktree; sequential merge on completion. Textual conflicts detected by git; semantic conflicts reviewed by agent or human. Runtime multi-agent coordination wiring remains M4 hardening work. | Industry standard (Codex, Cursor, Claude Code, VS Code). Maximum parallelism. Leverages mature git infrastructure. See §6.8. |
| **Graceful shutdown** | Adopted (M3) | Pluggable `ShutdownStrategy` protocol. Default: cooperative with 30s timeout. Agents check shutdown event at turn boundaries. Force-cancel after timeout. `INTERRUPTED` status for force-cancelled tasks. M4/M5: upgrade to checkpoint-and-stop. | Cross-platform (Windows `signal.signal()` fallback). Bounded shutdown time. Mirrors cooperative shutdown in §6.7. |
| **Template inheritance** | Adopted (M2.5) | `extends` field on `CompanyTemplate` triggers parent resolution at render time. `merge.py` merges configs by field type: scalars (child wins), config dicts (deep merge), agents (by `(role, department, merge_id)` key with `_remove` support), departments (by name). `_ParentEntry` dataclass tracks merge state. `DEFAULT_MERGE_DEPARTMENT = "engineering"` shared between merge and renderer. Circular chains detected via `frozenset` tracking; max depth = 10. | Enables template composition without copy-paste. Merge-by-key preserves parent order. `_remove` directive enables clean agent removal without workarounds. |
| **Pydantic alias for YAML directives** | Adopted (M2.5) | `Field(alias="_remove")` in `TemplateAgentConfig` — YAML uses `_remove: true`, Python accesses `agent.remove`. Keeps the YAML-facing name (underscore prefix signals internal directive) separate from the Python attribute name. | Underscore-prefixed YAML keys signal merge directives vs regular fields. Pydantic alias bridges the naming convention gap cleanly. |
| **Communication foundation** | Adopted (M4) | `MessageBus` protocol with `InMemoryMessageBus` backend (asyncio queues, pull-model `receive()` with shutdown signaling via `asyncio.Event`). `MessageDispatcher` routes to concurrent handlers via `asyncio.TaskGroup` with pre-allocated error collection. `AgentMessenger` per-agent facade auto-fills sender/timestamp/ID; deterministic direct-channel naming `@{sorted_a}:{sorted_b}`. `DeliveryEnvelope` for delivery tracking. `NotBlankStr` validation on all protocol boundary identifiers. | Pull-model avoids callback complexity and enables agents to consume at their own pace. Protocol + backend split enables future persistent/distributed bus implementations. Deterministic DM channel names prevent duplicates. See §5. |
| **Delegation & loop prevention** | Adopted (M4) | `HierarchyResolver` resolves org hierarchy from `Company` at construction (cycle-detected, `MappingProxyType`-frozen). `AuthorityValidator` checks chain-of-command + role permissions. `DelegationGuard` orchestrates five mechanisms (ancestry, depth, dedup, rate limit, circuit breaker) in sequence, short-circuiting on first rejection. `DelegationService` is synchronous (CPU-only); messaging integration deferred. Stateful mechanisms use injectable clock for deterministic testing. Task model extended with `parent_task_id` and `delegation_chain` fields. | Synchronous delegation avoids async complexity for CPU-only validation. Five-mechanism guard provides defence-in-depth against all loop patterns. Injectable clocks enable deterministic testing. See §5.4, §5.5. |
| **Task assignment** | Adopted (M4) | `TaskAssignmentStrategy` protocol with six concrete strategies: Manual (pre-designated), RoleBased (capability scoring via `AgentTaskScorer`), LoadBalanced (workload-aware with score tiebreaker), CostOptimized (cheapest-agent with score tiebreaker), Hierarchical (subordinate delegation via `HierarchyResolver`), Auction (bid = score × availability). `TaskAssignmentService` orchestrates with status validation, structured logging, and `STRATEGY_MAP` registry (`MappingProxyType`-wrapped singletons; five strategies — Hierarchical requires `build_strategy_map(hierarchy=...)`). Inactive agents filtered during scoring. | Pluggable strategies behind a protocol mirror the execution loop and conflict resolution patterns. Reuses `AgentTaskScorer` from routing subsystem. `MappingProxyType` registry matches existing immutability conventions. See §6.4. |
| **Conflict resolution** | Adopted (M4) | `ConflictResolver` protocol with async `resolve()` + sync `build_dissent_records()` split (resolve may call LLM, dissent record is pure construction). Four strategies: `AuthorityResolver` (seniority comparison iterating all N positions, hierarchy proximity tiebreaker via `get_lowest_common_manager`), `DebateResolver` (LLM judge via `JudgeEvaluator` protocol, authority fallback when absent), `HumanEscalationResolver` (stub, returns `ESCALATED_TO_HUMAN`), `HybridResolver` (LLM review + ambiguity escalation/authority fallback). `ConflictResolutionService` follows `DelegationService` pattern (`__slots__`, keyword-only constructor, `MappingProxyType`-wrapped resolver mapping, audit trail). `DissentRecord` preserves losing agent's reasoning. `Conflict.is_cross_department` is a `@computed_field` derived from positions. `HierarchyResolver` extended with `get_lowest_common_manager()` and `get_delegation_depth()`. | Protocol + strategy pattern enables adding new resolution approaches without modifying existing code. Async resolve accommodates LLM calls; sync dissent record avoids unnecessary async overhead. Shared `find_losers` utility prevents code duplication across strategies. See §5.6. |

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
| Persistent agent memory | No | No | Basic | **Yes (Mem0 initial, custom stack future — ADR-001)** |
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
- **LiteLLM**, **Litestar**, **MCP**, and **Mem0** (memory layer — ADR-001) give us battle-tested components for the hard parts
- The "company simulation" layer on top is our unique value and must be purpose-built

What we **plan to leverage** (not fork) — subject to evaluation:
- **LiteLLM** (selected) - Provider abstraction
- **Mem0** (selected, ADR-001) - Agent memory (initial backend; custom stack future)
- **Litestar** (selected) - API layer (see §15.4 rationale)
- **MCP** - Tool integration standard (strong candidate, emerging industry standard)
- **Pydantic** (selected) - Config validation and data models
- **Web UI framework** - TBD (Vue 3, React, Svelte, HTMX all under consideration)

> **Why Litestar over FastAPI?** Both are async-native Python frameworks with auto-generated OpenAPI docs and Pydantic support. FastAPI has a larger ecosystem and more community resources. However, Litestar provides significantly more built-in functionality that we would otherwise need to write and maintain ourselves:
>
> 1. **Channels plugin** — pub/sub WebSocket broadcasting with per-channel subscriptions, backpressure management, and subscriber backlog. FastAPI requires hand-rolling all WebSocket connection management.
> 2. **Class-based controllers** — group routes with shared guards, middleware, and configuration. Our 13 route groups map naturally to controllers. FastAPI only supports loose functions on routers.
> 3. **Native route guards** — declarative authorization at controller/route level. Essential for the approval queue and future security features. FastAPI requires `Depends()` on every route.
> 4. **Built-in middleware** — rate limiting, CSRF protection, GZip/Brotli compression, session handling, request logging. FastAPI requires third-party packages or custom code for each.
> 5. **Explicit dependency injection** — pytest-style named dependencies with scope control. Matches our testing approach. FastAPI's DI is implicit (function parameter magic).
>
> The ecosystem size gap is acceptable: our API is an internal orchestration interface, not a public web service. The bottleneck is LLM latency (seconds), not framework overhead (microseconds). Litestar's ~2x performance advantage in micro-benchmarks is a bonus, not the deciding factor. Python 3.14 is supported by both.

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
| 14 | Which memory layer library to use? | Medium | **Resolved** | Mem0 (initial) → custom stack (future) behind pluggable `MemoryBackend` protocol — see [ADR-001](docs/decisions/ADR-001-memory-layer.md) |
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
| Memory retrieval quality | Medium | Mem0 selected as initial backend (ADR-001). Protocol layer enables backend swap if retrieval quality insufficient. Pin version, test 3.14 compat in CI |
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
- [Gloaguen et al., "Evaluating AGENTS.md" (2026)](https://arxiv.org/abs/2602.11988) - Context files reduce success rates; non-inferable-only principle for system prompts
