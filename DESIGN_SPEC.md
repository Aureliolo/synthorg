# AI Company - High-Level Design Specification

> A framework for orchestrating autonomous AI agents within a virtual company structure, with configurable roles, hierarchies, communication patterns, and tool access.

---

## Table of Contents

1. [Vision & Philosophy](#1-vision--philosophy)
2. [Core Concepts](#2-core-concepts)
3. [Agent System](#3-agent-system)
4. [Company Structure](#4-company-structure)
5. [Communication Architecture](#5-communication-architecture)
6. [Task & Workflow Engine](#6-task--workflow-engine)
7. [Memory & Persistence](#7-memory--persistence)
8. [HR & Workforce Management](#8-hr--workforce-management)
9. [Model Provider Layer](#9-model-provider-layer)
10. [Cost & Budget Management](#10-cost--budget-management)
11. [Tool & Capability System](#11-tool--capability-system)
12. [Security & Approval System](#12-security--approval-system)
13. [Human Interaction Layer](#13-human-interaction-layer)
14. [Templates & Builder](#14-templates--builder)
15. [Technical Architecture](#15-technical-architecture)
16. [Research & Prior Art](#16-research--prior-art)
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
| **Provider Agnostic** | Any LLM backend: Claude API, OpenRouter, Ollama, custom endpoints |
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

> **Current state (M2):** Only the config layer exists as `AgentIdentity` (frozen Pydantic model in `core/agent.py`). The runtime state layer will be introduced in M3 when the agent execution engine is implemented. Non-optional identifier fields currently use `str` with `Field(min_length=1)` + a manual `@model_validator`; migration to `NotBlankStr` (from `core.types`) is planned.

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
    provider: "anthropic"            # example provider
    model_id: "claude-sonnet-4-6"    # example model — actual models TBD per agent/role
    temperature: 0.3
    max_tokens: 8192
    fallback_model: "openrouter/anthropic/claude-haiku"  # example fallback
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
| Intern/Junior | Execute assigned tasks only | Haiku / small local | $ |
| Mid | Execute + suggest improvements | Sonnet / medium local | $$ |
| Senior | Execute + design + review others | Sonnet / Opus | $$$ |
| Lead | All above + approve + delegate | Opus / Sonnet | $$$ |
| Principal/Staff | All above + architectural decisions | Opus | $$$$ |
| Director | Strategic decisions + budget authority | Opus | $$$$ |
| VP | Department-wide authority | Opus | $$$$ |
| C-Suite (CEO/CTO/CFO) | Company-wide authority + final approvals | Opus | $$$$ |

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
    suggested_model: "opus"
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
│   SQLite / PostgreSQL / File-based / Mem0    │
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
  backend: "sqlite"             # sqlite, postgresql, file (Mem0 is a memory layer on top, not a backend itself — see 15.2)
  options:
    retention_days: null         # null = forever
    max_memories_per_agent: 10000
    consolidation_interval: "daily"  # compress old memories
    shared_knowledge_base: true      # agents can access shared facts
```

---

## 8. HR & Workforce Management

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
│ Anthropic │OpenRouter │  Ollama   │ Custom  │
│  Adapter  │  Adapter  │  Adapter  │ Adapter │
├───────────┼───────────┼───────────┼─────────┤
│Claude API │ 400+ LLMs│ Local LLMs│ Any API │
│ Direct    │ via OR    │ Self-host │         │
└───────────┴───────────┴───────────┴─────────┘
```

### 9.2 Provider Configuration

> Note: Model IDs, pricing, and provider examples below are **illustrative**. Actual models, costs, and provider availability will be determined during implementation and should be loaded dynamically from provider APIs where possible.

```yaml
providers:
  anthropic:
    api_key: "${ANTHROPIC_API_KEY}"
    models:                        # example entries — real list loaded from provider
      - id: "claude-opus-4-6"
        alias: "opus"
        cost_per_1k_input: 0.015   # illustrative, verify at implementation time
        cost_per_1k_output: 0.075
        max_context: 200000
      - id: "claude-sonnet-4-6"
        alias: "sonnet"
        cost_per_1k_input: 0.003
        cost_per_1k_output: 0.015
        max_context: 200000
      - id: "claude-haiku-4-5"
        alias: "haiku"
        cost_per_1k_input: 0.0008
        cost_per_1k_output: 0.004
        max_context: 200000

  openrouter:
    api_key: "${OPENROUTER_API_KEY}"
    base_url: "https://openrouter.ai/api/v1"
    models:                        # example entries
      - id: "anthropic/claude-sonnet-4-6"
        alias: "or-sonnet"
      - id: "google/gemini-2.5-pro"
        alias: "or-gemini-pro"
      - id: "deepseek/deepseek-r1"
        alias: "or-deepseek"

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
  strategy: "smart"              # smart, cheapest, fastest, manual
  rules:
    - role_level: "C-Suite"
      preferred_model: "opus"
      fallback: "sonnet"
    - role_level: "Senior"
      preferred_model: "sonnet"
      fallback: "haiku"
    - role_level: "Junior"
      preferred_model: "haiku"
      fallback: "local-small"
    - task_type: "code_review"
      preferred_model: "sonnet"
    - task_type: "documentation"
      preferred_model: "haiku"
    - task_type: "architecture"
      preferred_model: "opus"
  fallback_chain:
    - "anthropic"
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
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "input_tokens": 4500,
  "output_tokens": 1200,
  "total_tokens": 5700,
  "cost_usd": 0.0315,
  "timestamp": "2026-02-27T10:30:00Z"
}
```

> **Implementation note:** `total_tokens` is a `@computed_field` property that returns `input_tokens + output_tokens` — no stored field or validator needed. Spending summary models (`AgentSpending`, `DepartmentSpending`, `PeriodSpending`) each independently define `total_cost_usd`, `total_input_tokens`, `total_output_tokens`, and `record_count` fields. Extracting a shared `_SpendingTotals` base is a planned convention (see §15.5).

### 10.3 CFO Agent Responsibilities

The CFO agent (when enabled) acts as a cost management system:

- Monitors real-time spending across all agents
- Alerts when departments approach budget limits
- Suggests model downgrades when budget is tight
- Reports daily/weekly spending summaries
- Recommends hiring/firing based on cost efficiency
- Blocks tasks that would exceed remaining budget
- Optimizes model routing for cost/quality balance

### 10.4 Cost Controls

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
    downgrade_map:             # example — aliases reference configured models
      opus: "sonnet"
      sonnet: "haiku"
      haiku: "local-small"
```

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

When the LLM requests multiple tool calls in a single turn, `ToolInvoker.invoke_all` currently executes them **sequentially**. Migration to `asyncio.TaskGroup` for parallel structured concurrency is planned (see §15.5). Recoverable errors are captured as `ToolResult(is_error=True)` without aborting remaining invocations; non-recoverable errors (`MemoryError`, `RecursionError`) propagate immediately and abort the sequence.

`BaseTool.parameters_schema` deep-copies the caller-supplied schema at construction and wraps it in `MappingProxyType` for read-only enforcement; the property returns a deep copy on access to prevent mutation of internal state. `ToolInvoker` deep-copies arguments at the tool execution boundary before passing them to `tool.execute()`. `MappingProxyType` wrapping is also used in `ToolRegistry` for its internal collections.

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

Agents can earn higher tool access over time:

```yaml
trust:
  enabled: true
  initial_level: "sandboxed"
  promotion_criteria:
    sandboxed_to_restricted:
      tasks_completed: 5
      quality_score_min: 7.0
    restricted_to_standard:
      tasks_completed: 20
      quality_score_min: 8.0
      time_active_days: 7
    standard_to_elevated:
      requires_human_approval: true
```

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
      model: "opus"
      personality_preset: "visionary_leader"

    - role: "full_stack_developer"
      name: "{{ dev1_name | auto }}"
      level: "senior"
      model: "sonnet"
      personality_preset: "pragmatic_builder"

    - role: "full_stack_developer"
      name: "{{ dev2_name | auto }}"
      level: "mid"
      model: "haiku"
      personality_preset: "eager_learner"

    - role: "product_manager"
      name: "{{ pm_name | auto }}"
      model: "sonnet"
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
  - 1x Lead (Opus)
  - 2x Senior Dev (Sonnet)
  - 2x Junior Dev (Haiku)

? Add QA? yes
  - 1x QA Lead (Sonnet)
  - 1x QA Engineer (Haiku)

? Model providers:
  [x] Anthropic Claude
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
| **Language** | Python 3.14+ | Best AI/ML ecosystem, all major frameworks use it, LiteLLM/Mem0/MCP all Python-native. PEP 649 native lazy annotations, PEP 758 except syntax. |
| **API Framework** | FastAPI | Async-native, WebSocket support, auto OpenAPI docs, high performance, type-safe with Pydantic |
| **LLM Abstraction** | LiteLLM | 100+ providers, unified API, built-in cost tracking, retries/fallbacks |
| **Agent Memory** | Mem0 + SQLite | Mem0 for semantic/episodic memory, SQLite for structured data. Upgrade to Postgres later |
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
│       ├── engine/                  # Core engines (M3+)
│       │   ├── errors.py           # Engine error hierarchy
│       │   ├── prompt.py           # System prompt builder
│       │   ├── prompt_template.py  # System prompt Jinja2 templates
│       │   ├── task_execution.py   # TaskExecution + StatusTransition
│       │   ├── context.py          # AgentContext + AgentContextSnapshot
│       │   ├── agent_engine.py     # Agent execution loop (M3)
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
│       │   ├── events.py           # All event constants (domain-scoped via naming)
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
│       │   ├── invoker.py          # Tool invocation (sequential execution)
│       │   ├── errors.py           # Tool error hierarchy
│       │   ├── examples/           # Example tool implementations
│       │   │   └── echo.py        # Echo tool (for testing)
│       │   ├── sandbox.py          # Sandboxed execution (M3)
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
│       │   ├── spending_summary.py # AgentSpending, DepartmentSpending, PeriodSpending
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
| Language | Python 3.14+ | TypeScript, Go, Rust | AI ecosystem, LiteLLM/Mem0 are Python, PEP 649 lazy annotations, PEP 758 except syntax |
| API | FastAPI | Flask, Django, aiohttp | Async native, Pydantic integration, auto docs, WebSocket support |
| LLM Layer | LiteLLM | Direct APIs, OpenRouter only | 100+ providers, cost tracking, fallbacks, load balancing built-in |
| Memory | Mem0 + SQLite | Custom, ChromaDB, Pinecone | Production-proven (26% accuracy boost), supports all memory types, open-source |
| Message Bus | asyncio queues → Redis | Kafka, RabbitMQ, NATS | Start simple, Redis well-supported, Kafka overkill for local |
| Config | YAML + Pydantic | JSON, TOML, Python dicts | Human-friendly, strict validation, good IDE support |
| CLI | Typer | Click, argparse, Fire | Built on Click, auto-completion, type hints |
| Web UI | Vue 3 | React, Svelte, HTMX | Simpler than React for dashboards, good with FastAPI |

### 15.5 Pydantic Model Conventions (M2.5)

These conventions were established during the M0–M2 review cycle. **Adopted** conventions are already used throughout the codebase. **Planned** conventions are approved decisions for new/migrated code but not yet applied everywhere.

| Convention | Status | Decision | Rationale |
|------------|--------|----------|-----------|
| **Immutability strategy** | Adopted | `copy.deepcopy()` at construction + `MappingProxyType` wrapping for non-Pydantic internal collections (registries, `BaseTool`). For Pydantic frozen models: `frozen=True` prevents field reassignment; `copy.deepcopy()` at system boundaries (tool execution, LLM provider serialization) prevents nested mutation. No MappingProxyType inside Pydantic models (serialization friction). | Deep-copy at construction fully isolates nested structures; `MappingProxyType` enforces read-only access. Boundary-copy for Pydantic models is simple, centralized, and Pydantic-native. A future CPython built-in immutable mapping type (e.g. `frozendict`) would provide zero-friction field-level immutability when available. |
| **Config vs runtime split** | Adopted (M3) | Frozen models for config/identity; `model_copy(update=...)` for runtime state transitions | `TaskExecution` and `AgentContext` (in `engine/`) are frozen Pydantic models that use `model_copy(update=...)` for copy-on-write state transitions without re-running validators (per Pydantic `model_copy` semantics). Config layer (`AgentIdentity`, `Task`) remains unchanged. |
| **Derived fields** | Adopted | `@computed_field` instead of stored + validated | Eliminates redundant storage and impossible-to-fail validators. `TokenUsage.total_tokens` migrated from stored `Field` + `@model_validator` to `@computed_field` property. |
| **String validation** | Planned | `NotBlankStr` type from `core.types` for all identifiers | Eliminates per-model `@model_validator` boilerplate for whitespace checks. `NotBlankStr` is defined but models still use `Field(min_length=1)` + manual validators. |
| **Shared field groups** | Planned | Extract common field sets into base models (e.g. `_SpendingTotals`) | Prevents field duplication across spending summary models. Not yet implemented — each model independently defines fields. |
| **Event constants** | Adopted (flat) | Single `events.py` module with domain-scoped naming (e.g. `PROVIDER_CALL_START`, `BUDGET_RECORD_ADDED`) | Current approach uses a single module. Splitting into per-domain submodules may be revisited when the file exceeds ~200 constants. |
| **Parallel tool execution** | Planned | `asyncio.TaskGroup` in `ToolInvoker.invoke_all` | Structured concurrency with proper cancellation semantics. Currently sequential; migration planned for M3 when the agent engine needs concurrent tool calls. |

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
| Persistent agent memory | No | No | Basic | **Yes (Mem0 candidate)** |
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

### 16.3 Build vs Fork Decision

**Recommendation: Build from scratch, leverage libraries.**

Rationale:
- No existing framework covers even 50% of our requirements
- Our core differentiators (HR, budget, security ops, deep personalities, progressive trust) don't exist in any framework
- Forking MetaGPT or CrewAI would mean fighting their architecture while adding our features
- **LiteLLM**, **Mem0**, **FastAPI**, and **MCP** give us battle-tested components for the hard parts
- The "company simulation" layer on top is our unique value and must be purpose-built

What we **plan to leverage** (not fork) — subject to evaluation:
- **LiteLLM** (candidate) - Provider abstraction
- **Mem0** (candidate) - Agent memory
- **FastAPI** (candidate) - API layer
- **MCP** - Tool integration standard (strong candidate, emerging industry standard)
- **Pydantic** (candidate) - Config validation and data models
- **Typer** (candidate) - CLI
- **Web UI framework** - TBD (Vue 3, React, Svelte, HTMX all under consideration)

---

## 17. Open Questions & Risks

### 17.1 Open Questions

| # | Question | Impact | Notes |
|---|----------|--------|-------|
| 1 | How deep should agent personality affect output? | Medium | Too deep = inconsistent, too shallow = all agents feel the same |
| 2 | What is the optimal meeting format for multi-agent? | High | Determines quality of collaborative decisions |
| 3 | How to handle context window limits for long tasks? | High | Agents may lose track of complex multi-file changes |
| 4 | Should agents be able to create/modify other agents? | Medium | CTO "hires" a dev by creating a new agent config |
| 5 | How to handle conflicting agent opinions? | High | Two agents disagree on architecture - who wins? |
| 6 | What metrics define "good" agent performance? | Medium | Needed for HR/hiring/firing decisions |
| 7 | How to prevent agent communication loops? | High | Agent A asks Agent B who asks Agent A... |
| 8 | Optimal message bus for local-first architecture? | Medium | asyncio queues vs Redis vs embedded broker |
| 9 | How to handle code execution safely? | High | Sandboxing strategy, Docker vs WASM vs subprocess |
| 10 | What's the minimum viable meeting set? | Low | Standup + planning + review as minimum? |

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
| Data loss on crash | Medium | WAL mode SQLite, periodic snapshots, recovery system |

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
| Conflict resolution protocol | High | Structured process when agents disagree |
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
