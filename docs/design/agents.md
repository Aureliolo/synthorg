---
title: Agents & HR
description: Agent identity system, seniority levels, role catalog, hiring, firing, performance tracking, and promotions in the SynthOrg framework.
---

# Agents & HR

## Agent Identity Card

Every agent has a comprehensive identity. At the design level, agent data splits into two
layers:

Config (immutable)
:   Identity, personality, skills, model preferences, tool permissions, and authority.
    Defined at hire time, changed only by explicit reconfiguration. Represented as frozen
    Pydantic models.

Runtime state (mutable-via-copy)
:   Current status, active task, conversation history, and execution metrics. Evolves during
    agent operation. Represented as Pydantic models using `model_copy(update=...)` for state
    transitions -- never mutated in place.

### Personality Dimensions

Personality is split into two tiers:

=== "Big Five (OCEAN-variant)"

    Float values (0.0--1.0) used for **internal compatibility scoring only** (not injected
    into prompts). `stress_response` replaces traditional neuroticism with inverted polarity
    (1.0 = very calm). Scored by `core/personality.py`.

    | Dimension | Range | Description |
    |-----------|-------|-------------|
    | `openness` | 0.0--1.0 | Curiosity, creativity |
    | `conscientiousness` | 0.0--1.0 | Thoroughness, reliability |
    | `extraversion` | 0.0--1.0 | Assertiveness, sociability |
    | `agreeableness` | 0.0--1.0 | Cooperation, empathy |
    | `stress_response` | 0.0--1.0 | Emotional stability (1.0 = very calm) |

    **Compatibility scoring** (weighted composite, result clamped to [0, 1]):

    - **60% Big Five similarity:** `openness`, `conscientiousness`, `agreeableness`,
      `stress_response` use `1 - |diff|`; `extraversion` uses a tent-function peaking
      at 0.3 diff (complementary extraverts collaborate better than identical ones)
    - **20% Collaboration alignment:** ordinal adjacency
      (`INDEPENDENT` ↔ `PAIR` ↔ `TEAM`); scored 1.0 for same, 0.5 for adjacent, 0.0
      for opposite
    - **20% Conflict approach:** constructive pairs score 1.0, destructive pairs 0.2,
      mixed 0.4--0.6. Uses `itertools.combinations` for team-level averaging

=== "Behavioral Enums"

    Injected into system prompts as natural-language labels that LLMs respond to:

    | Enum | Values |
    |------|--------|
    | `DecisionMakingStyle` | `analytical`, `intuitive`, `consultative`, `directive` |
    | `CollaborationPreference` | `independent`, `pair`, `team` |
    | `CommunicationVerbosity` | `terse`, `balanced`, `verbose` |
    | `ConflictApproach` | `avoid`, `accommodate`, `compete`, `compromise`, `collaborate` (Thomas-Kilmann model) |

### Agent Configuration Example

???+ example "Full agent identity YAML"

    ```yaml
    # --- Config layer — AgentIdentity (frozen) ---
    agent:
      id: "uuid"
      name: "Sarah Chen"
      role: "Senior Backend Developer"
      department: "Engineering"
      level: "Senior"
      personality:
        traits:
          - analytical
          - detail-oriented
          - pragmatic
        communication_style: "concise and technical"
        risk_tolerance: "low"
        creativity: "medium"
        description: >
          Sarah is a methodical backend developer who prioritizes clean
          architecture and thorough testing. She pushes back on shortcuts
          and advocates for proper error handling. Prefers Pythonic solutions.
        # Big Five (OCEAN-variant) — internal scoring (0.0-1.0)
        openness: 0.4
        conscientiousness: 0.9
        extraversion: 0.3
        agreeableness: 0.5
        stress_response: 0.75
        # Behavioral enums — injected into system prompts
        decision_making: "analytical"
        collaboration: "independent"
        verbosity: "balanced"
        conflict_approach: "compromise"
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
        provider: "example-provider"
        model_id: "example-medium-001"
        temperature: 0.3
        max_tokens: 8192
        fallback_model: "openrouter/example-medium-001"
      memory:
        type: "persistent"       # persistent, project, session, none
        retention_days: null     # null = forever
      tools:
        access_level: "standard" # sandboxed | restricted | standard | elevated | custom
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
        budget_limit: 5.00
      autonomy_level: null       # full, semi, supervised, locked (overrides defaults)
      hiring_date: "2026-02-27"
      status: "active"           # active, on_leave, terminated
    ```

### Runtime State

The runtime state layer (in `engine/`) tracks execution progress using frozen models
with `model_copy`:

- **TaskExecution** wraps a Task with evolving execution state: status transitions,
  accumulated cost (`TokenUsage`), turn count, and timestamps.
- **AgentContext** wraps `AgentIdentity` + `TaskExecution` with a unique execution ID,
  conversation history, cost accumulation, turn limits, and timing.

---

## Seniority & Authority Levels

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

---

## Role Catalog

The role catalog is extensible -- users can add [custom roles](#dynamic-roles) via config.
The built-in catalog covers common organizational roles:

=== "C-Suite / Executive"

    - **CEO** -- Overall strategy, final decision authority, cross-department coordination
    - **CTO** -- Technical vision, architecture decisions, technology choices
    - **CFO** -- Budget management, cost optimization, resource allocation
    - **COO** -- Operations, process optimization, workflow management
    - **CPO** -- Product strategy, roadmap, feature prioritization

=== "Product & Design"

    - **Product Manager** -- Requirements, user stories, prioritization, stakeholder communication
    - **UX Designer** -- User research, wireframes, user flows, usability
    - **UI Designer** -- Visual design, component design, design systems
    - **UX Researcher** -- User interviews, analytics, A/B test design
    - **Technical Writer** -- Documentation, API docs, user guides

=== "Engineering"

    - **Software Architect** -- System design, technology decisions, patterns
    - **Frontend Developer** (Junior/Mid/Senior) -- UI implementation, components, state management
    - **Backend Developer** (Junior/Mid/Senior) -- APIs, business logic, databases
    - **Full-Stack Developer** (Junior/Mid/Senior) -- End-to-end implementation
    - **DevOps/SRE Engineer** -- Infrastructure, CI/CD, monitoring, deployment
    - **Database Engineer** -- Schema design, query optimization, migrations
    - **Security Engineer** -- Security audits, vulnerability assessment, secure coding

=== "Quality Assurance"

    - **QA Lead** -- Test strategy, quality gates, release readiness
    - **QA Engineer** -- Test plans, manual testing, bug reporting
    - **Automation Engineer** -- Test frameworks, CI integration, E2E tests
    - **Performance Engineer** -- Load testing, profiling, optimization

=== "Data & Analytics"

    - **Data Analyst** -- Metrics, dashboards, business intelligence
    - **Data Engineer** -- Pipelines, ETL, data infrastructure
    - **ML Engineer** -- Model training, inference, MLOps

=== "Operations & Support"

    - **Project Manager** -- Timelines, dependencies, risk management, status tracking
    - **Scrum Master** -- Agile ceremonies, impediment removal, team health
    - **HR Manager** -- Hiring recommendations, team composition, performance tracking
    - **Security Operations** -- Request validation, safety checks, approval workflows

=== "Creative & Marketing"

    - **Content Writer** -- Blog posts, marketing copy, social media
    - **Brand Strategist** -- Messaging, positioning, competitive analysis
    - **Growth Marketer** -- Campaigns, analytics, conversion optimization

---

## Dynamic Roles

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

## Hiring Process

The HR system manages the agent workforce dynamically:

1. HR agent (or human) identifies a skill gap or workload issue
2. HR generates **candidate cards** based on team needs:
    - What skills are underrepresented?
    - What seniority level is needed?
    - What personality would complement the team?
    - What model/provider fits the budget?
3. Candidate cards are presented for approval (to CEO or human)
4. Approved candidates are instantiated and onboarded
5. Onboarding includes: company context, project briefing, team introductions

!!! info "Design decisions ([ADR-002](../architecture/decisions.md) D8)"

    - **D8.1 -- Source:** Templates + LLM customization. Templates for common roles
      (reuses existing [template system](organization.md#template-system)). LLM generates
      config for novel roles not covered by templates. Approval gate catches invalid/bad
      configs before instantiation.
    - **D8.2 -- Persistence:** Operational store via `PersistenceBackend`. YAML stays as
      bootstrap seed -- operational store wins for runtime state. Enables rehiring and
      auditable history.
    - **D8.3 -- Hot-plug:** Agents are hot-pluggable at runtime via a dedicated
      company/registry service (not `AgentEngine`, which remains the per-agent task runner).
      Thread-safe registry, wired into message bus + tools + budget.

---

## Firing / Offboarding

Offboarding is triggered by: budget cuts, poor performance metrics, project completion, or
human decision.

1. Agent's memory is archived (not deleted)
2. Active tasks are reassigned
3. Team is notified

!!! info "Design decisions ([ADR-002](../architecture/decisions.md) D9, D10)"

    - **D9 -- Task Reassignment:** Pluggable `TaskReassignmentStrategy` protocol. Initial
      strategy: queue-return -- tasks return to unassigned queue, existing `TaskRoutingService`
      re-routes with priority boost for reassigned tasks. Future strategies:
      same-department/lowest-load, manager-decides (LLM), HR agent decides.
    - **D10 -- Memory Archival:** Pluggable `MemoryArchivalStrategy` protocol. Initial
      strategy: full snapshot, read-only. Pipeline: retrieve all memories, archive to
      `ArchivalStore`, selectively promote semantic+procedural memories to
      `OrgMemoryBackend` (rule-based), clean hot store, mark agent TERMINATED. Rehiring
      restores archived memories into a new `AgentIdentity`. Future strategies: selective
      discard, full-accessible.

---

## Performance Tracking

The framework tracks detailed per-agent metrics:

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

???+ note "Design decisions ([ADR-002](../architecture/decisions.md) D2, D3, D11, D12)"

    **D2 -- Quality Scoring:** Pluggable `QualityScoringStrategy` protocol. Initial
    strategy: layered combination --

    1. **FREE:** Objective CI signals (test pass/fail, lint, coverage delta)
    2. **~$1/day:** Small-model LLM judge (different family than agent) evaluates output
       vs acceptance criteria
    3. **On-demand:** Human override via API, highest weight

    Start with Layer 1 only; add layers incrementally. Future strategies: CI-only,
    LLM-only, human-only.

    ---

    **D3 -- Collaboration Scoring:** Pluggable `CollaborationScoringStrategy` protocol.
    Initial strategy: automated behavioral telemetry --

    ```
    collaboration_score = weighted_average(
        delegation_success_rate,
        delegation_response_latency,
        conflict_resolution_constructiveness,
        meeting_contribution_rate,
        loop_prevention_score,
        handoff_completeness
    )
    ```

    Weights are configurable per-role. Optional: periodic LLM sampling (1%) for
    calibration + human override via API. Future strategies: LLM evaluation, peer
    ratings, human-provided.

    ---

    **D11 -- Rolling Windows:** Pluggable `MetricsWindowStrategy` protocol. Initial
    strategy: multiple simultaneous windows --

    - **7d** for acute regressions
    - **30d** for sustained patterns
    - **90d** for baseline/drift

    Minimum 5 data points per window; below that, the system reports "insufficient data."
    Future strategies: fixed single window, per-metric configurable.

    ---

    **D12 -- Trend Detection:** Pluggable `TrendDetectionStrategy` protocol. Initial
    strategy: Theil-Sen regression slope per window + configurable thresholds classify
    trends as improving/stable/declining. Theil-Sen has 29.3% outlier breakdown (tolerates
    ~1 in 3 bad data points). Minimum 5 data points. Future strategies:
    period-over-period, OLS regression, threshold-only.

---

## Promotions & Demotions

Agents can move between seniority levels based on performance:

- **Promotion criteria:** Sustained high quality scores, task complexity handled, peer feedback
- **Demotion criteria:** Repeated failures, quality drops, cost inefficiency
- Promotions can unlock higher [tool access levels](operations.md#tool-access-levels)
- Model upgrades/downgrades may accompany level changes (configurable, see [auto-downgrade](operations.md#cost-controls))

!!! info "Design decisions ([ADR-002](../architecture/decisions.md) D13, D14, D15)"

    - **D13 -- Promotion Criteria:** Pluggable `PromotionCriteriaStrategy` protocol. Initial
      strategy: configurable threshold gates. `ThresholdEvaluator` with
      `min_criteria_met: int` (N of M) + `required_criteria: list[str]`. Setting `min=total`
      gives AND; `min=1` gives OR. Default: junior-to-mid = 2 of 3 criteria,
      mid-to-senior = all.
    - **D14 -- Promotion Approval:** Pluggable `PromotionApprovalStrategy` protocol. Initial
      strategy: senior+ requires human approval. Junior-to-mid auto-promotes (low cost
      impact: small-to-medium ~4x). Demotions: auto-apply for cost-saving (model downgrade),
      human approval for authority-reducing demotions.
    - **D15 -- Model Mapping:** Pluggable `ModelMappingStrategy` protocol. Initial strategy:
      default ON (`hr.promotions.model_follows_seniority: true`). Model changes at task
      boundaries only (never mid-execution, consistent with
      [auto-downgrade](operations.md)). Per-agent `preferred_model` overrides seniority
      default. Smart routing still uses cheap models for simple tasks regardless of seniority.
