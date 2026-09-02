---
title: HR & Agent Lifecycle
description: How an agent joins the roster, is measured on it, and leaves it. Role catalog, reporting-graph authority, dynamic roles, hiring, pruning, offboarding, performance tracking, and evolution.
---

# HR & Agent Lifecycle

How an agent joins the roster, how it is measured while it is on it, and how it
leaves. This matters to the build for one reason: the roster decides who can be
dispatched a unit of work and who can be selected to check a unit somebody else
produced. A role nobody holds is a plan item nobody can own, and a
[gate role](agents.md#built-in-roles) nobody holds parks a finished deliverable
rather than letting it through unchecked.

Nothing self-hires and nothing self-fires. Hiring is an approval item, pruning
is an approval item, and the evolution pipeline ships with identity mutation off
and a review gate on.

See [Agents](agents.md) for the identity layer (skills, tool namespaces, identity versioning).

## Authority: role and reporting graph

Authority is not a scalar rank. It derives from an agent's **role** and its position in the organisation's **reporting graph**. Each `Role` declares an optional `reports_to` (the role name of its supervisor); the CEO role sits at the root with `reports_to = None`.

`core/authority.py` reads that graph:

- `role_depth(role)`: distance from the CEO root (CEO is 0, its reports 1, and so on). A role the catalog cannot resolve, or whose chain never reaches a root, takes a depth larger than any real chain, so an unrecognised position never wins an authority contest by default.
- `reporting_chain(role)`: the ordered chain of supervisors up to the root.
- `compare_authority(a, b)`: a sign comparison by reporting depth, negative when `a` is junior to `b`, zero at equal depth, positive when `a` is more senior.

Consumers that need "who is more senior" (owner selection, plan-review panel selection, department-head detection) compare reporting depth via these helpers rather than reading a per-agent level. A role's model capability is a separate, independent axis driven by the work's capability demand (see [Providers](providers.md)), not by org position.

---

## Role Catalog

The role catalog is extensible; users can add [custom roles](#dynamic-roles) via config.
The built-in catalog covers common organisational roles:

=== "C-Suite / Executive"

    - **CEO**: Overall strategy, final decision authority, cross-department coordination
    - **CTO**: Technical vision, architecture decisions, technology choices
    - **CFO**: Budget management, cost optimisation, resource allocation
    - **COO**: Operations, process optimisation, workflow management
    - **CPO**: Product strategy, roadmap, feature prioritisation

=== "Product & Design"

    - **Product Manager**: Requirements, user stories, prioritisation, stakeholder communication
    - **UX Designer**: User research, wireframes, user flows, usability
    - **UI Designer**: Visual design, component design, design systems
    - **UX Researcher**: User interviews, analytics, A/B test design
    - **Technical Writer**: Documentation, API docs, user guides

=== "Engineering"

    - **Software Architect**: System design, technology decisions, patterns
    - **Frontend Developer** (Junior/Mid/Senior): UI implementation, components, state management
    - **Backend Developer** (Junior/Mid/Senior): APIs, business logic, databases
    - **Full-Stack Developer** (Junior/Mid/Senior): End-to-end implementation
    - **DevOps/SRE Engineer**: Infrastructure, CI/CD, monitoring, deployment
    - **Database Engineer**: Schema design, query optimisation, migrations
    - **Security Engineer**: Security audits, vulnerability assessment, secure coding

=== "Quality Assurance"

    - **QA Lead**: Test strategy, quality gates, release readiness
    - **QA Engineer**: Test plans, manual testing, bug reporting
    - **Automation Engineer**: Test frameworks, CI integration, E2E tests
    - **Performance Engineer**: Load testing, profiling, optimisation
    - **Red Team**: Adversarial review of high-stakes deliverables
    - **Completion Reviewer**: Independent peer review at the completion oracle

    The last two judge finished work rather than performing it, so a holder
    reaches every project rather than the one team it is staffed on, and each
    gate selects one per review. They are staffed exactly like any other role;
    an org that staffs neither parks its reviewed work and says which role is
    missing. See [Built-in Roles](agents.md#built-in-roles).

=== "Data & Analytics"

    - **Data Analyst**: Metrics, dashboards, business intelligence
    - **Data Engineer**: Pipelines, ETL, data infrastructure
    - **ML Engineer**: Model training, inference, MLOps

=== "Operations & Support"

    - **Project Manager**: Timelines, dependencies, risk management, status tracking
    - **Scrum Master**: Sprint cadence, impediment removal, team health
    - **HR Manager**: Hiring recommendations, team composition, performance tracking
    - **Security Operations**: Request validation, safety checks, approval workflows

=== "Creative & Marketing"

    - **Content Writer**: Blog posts, marketing copy, social media
    - **Brand Strategist**: Messaging, positioning, competitive analysis
    - **Growth Marketer**: Campaigns, analytics, conversion optimisation

---

## Dynamic Roles

Users can define custom roles via config:

```yaml
custom_roles:
  - name: "Blockchain Developer"
    department: "Engineering"
    skills: ["solidity", "web3", "smart-contracts"]
    system_prompt_template: "blockchain_dev.md"
    reports_to: "CTO"
    suggested_model: "large"
```

## Hiring Process

One condition opens a hire: an unstaffed [gate role](agents.md#built-in-roles).
A gate that finds nobody holding its role parks the work, and the
review-staffing sweep turns that park into an approval item. There is no
skill-gap detector and no endpoint an operator posts a vacancy to; an operator
who wants another working agent edits the roster directly (see
[Changing Headcount](organization.md#changing-headcount)). Nothing self-hires.

Once a request exists, `HiringService` runs it through five steps:

1. **Request.** `create_request` records the role, department, reason, and any
   budget ceiling. For a gate role it refuses a second request while one is
   already on its way to an agent, so two sweeps cannot open two vacancies for
   the one role. The guard sits on the invariant rather than on the caller, so
   a caller added later inherits it instead of re-deciding it.
2. **Candidate.** `generate_candidate` builds a `CandidateCard` from the role's
   catalog defaults.
3. **Approval.** The card is submitted as an `ORG_HIRE` approval item, whose
   risk level comes from the risk map rather than being restated at the
   submission site. A deployment with no approval store configured has nowhere
   to put the question, and the request is auto-approved.
4. **Decision.** Deciding the approval is what hires.
   `api/controllers/_approval_org_hire.py` picks the decision up in the approval
   fan-out, moves the request to APPROVED, and calls `instantiate_agent`, which
   registers the identity and runs onboarding; a rejection moves it to REJECTED
   and registers nobody. A failure to instantiate is surfaced, never swallowed,
   because a hire that silently did not land is indistinguishable from one
   nobody approved.
5. **Onboarding.** A checklist of `company_context`, `project_briefing` and
   `team_introductions`.

The agent MCP surface reaches neither end of this pipeline. It refuses to decide
an `org:hire` or `org:fire` approval, and it refuses to grant a gate role at all,
because holding one confers judging authority over other agents' work. Both stay
on the operator's own REST path.

### What model a new hire runs on

The pair is part of what the operator approves, not a standing setting the
hire reads afterwards. It decides what the agent can do and what it costs for
as long as the agent exists, and it depends on the role being filled and on
what the operator has actually configured, so one org-wide value cannot answer
it: it gave every hire the same answer, and gave every hire NO answer whenever
it was unset, which is how an approval came to be raised for a hire the system
would then refuse.

`hr/hire_model_proposal.py` proposes the pair instead, running the same
capability matcher the setup wizard runs when a template roster is filled out
(`templates/model_matcher.py`), scored against the operator's own configured
providers and biased by the company's `model_spend_profile`.

The **alternatives** are the operator's own catalogue rather than a second
opinion derived from it: every tool-capable configured model is offered,
cheapest first behind the recommendation, capped at eight. Re-running the
matcher under different optimisation axes looked like the richer answer and is
not, because the axes routinely converge on one model: an operator who wanted a
different one would be shown several labels for the same pair and no way to
change it. Tool capability is the only filter, because it is the one hard
property an agent's model must have; everything else is preference, and the
preference is the operator's to exercise here.

The approval carries that fork as its evidence package's `options`, so the
operator overrides the recommendation on the approval itself, in the drawer,
without leaving it. Each option's id IS the serialised pair, so the pick
decodes straight back to the binding with no lookup table between them. The
recommendation is stamped onto the request when the approval is raised, so an
approval taken without touching the options still has a binding;
`_approval_org_hire.py` replaces it with the operator's pick before the
approve transition, since the pick is part of the decision rather than an edit
to a decided request.

Nothing auto-picks a provider: every option names both halves, every option
came from the operator's own catalogue, and a hire whose request carries no
pair is refused at instantiation rather than registering an agent that joins
the roster looking staffed and fails every dispatch.

### Hiring for an unstaffed gate role

A gate that finds nobody holding its role parks the work and names the
condition; it does not ask for anybody itself. The ask belongs to the
review-staffing sweep, which reads every such park:
`ReviewStaffingReconciler._ensure_hire_open` keeps exactly one in-flight
request per role, org-wide and never per project, opens the ordinary approval
item, and notifies the operator naming the unstaffed role. Nothing self-hires.
The ask is conditional on the hiring pipeline being wired: a boot with no
approval store has none, and the sweep then still releases what it can and
still names the unstaffed role, it just cannot ask for anybody.
See [Nobody holds the role](verification-quality.md#nobody-holds-the-role).

!!! info "Design decisions ([Decision Log](../architecture/decisions.md) D8)"

    - **D8.1: Source.** A candidate card is built from the role catalog's own
      defaults. Template presets (reusing the
      [template system](organization.md#template-system)) and LLM customisation
      for novel roles are intent, not built. Either way the approval gate
      catches an invalid candidate before instantiation.
    - **D8.2: Persistence.** Operational store via `PersistenceBackend`. YAML stays as
      bootstrap seed; operational store wins for runtime state. Enables rehiring and
      auditable history.
    - **D8.3: Hot-plug.** Agents are hot-pluggable at runtime via a dedicated
      company/registry service (not `AgentEngine`, which remains the per-agent task runner).
      Thread-safe registry, wired into message bus + tools + budget.

---

## Pruning

The pruning service automates performance-driven agent removal with mandatory human approval.

- **`PruningPolicy`** protocol with two implementations:
  - `ThresholdPruningPolicy`: prunes agents whose mean completion-oracle verdict sits below the quality threshold for N+ consecutive windows (7d/30d/90d).
  - `TrendPruningPolicy`: prunes agents with declining Theil-Sen trend across all three windows.
- **`PruningService`** runs as a periodic background task, evaluates all active agents, and creates CRITICAL-risk approval items for eligible candidates.
- On human approval, delegates to `OffboardingService` with `FiringReason.PERFORMANCE`.
- Approval deduplication prevents multiple pending approvals per agent.
- Transient offboarding failures are retried on subsequent cycles.

Module: `src/synthorg/hr/pruning/` (models, policy, service).

## Firing / Offboarding

Offboarding is triggered by: budget cuts, poor performance metrics, project completion, or
human decision.

1. Agent's memory is archived (not deleted)
2. Active tasks are reassigned
3. Team is notified

!!! info "Design decisions ([Decision Log](../architecture/decisions.md) D9, D10)"

    Each decision below names the protocol that is currently implemented and the
    concrete `Initial strategy` that the default factory wires. "Initial
    strategy" is the shipped default, not aspirational scaffolding;
    operators replace it by registering an alternative strategy on the
    relevant factory.

    - **D9: Task Reassignment.** Pluggable `TaskReassignmentStrategy` protocol. Initial
      strategy: queue-return (concrete: `QueueReturnStrategy` in
      `src/synthorg/hr/queue_return_strategy.py`); tasks return to unassigned queue,
      existing `TaskRoutingService` re-routes with priority boost for reassigned tasks.
      Future strategies on the backlog: same-department / lowest-load, manager-decides
      (LLM), HR agent decides.
    - **D10: Memory Archival.** Pluggable `MemoryArchivalStrategy` protocol. Initial
      strategy: full snapshot, read-only (concrete: `FullSnapshotStrategy` in
      `src/synthorg/hr/full_snapshot_strategy.py`). Pipeline: retrieve all memories,
      archive to `ArchivalStore`, selectively promote semantic+procedural memories to
      `OrgMemoryBackend` (rule-based), clean hot store, mark agent TERMINATED. Rehiring
      restores archived memories into a new `AgentIdentity`. Future strategies on the
      backlog: selective discard, full-accessible.

## Performance Tracking

Performance data is exposed via three API sub-routes on `/api/v1/agents/{agent_id}` (the agent's stable id):

| Sub-route | Response model | Description |
|-----------|---------------|-------------|
| `GET /performance` | `AgentPerformanceSummary` | Flat summary: tasks completed (total/7d/30d), success rate, cost per task, quality score, trend direction (plus raw window metrics and trend results) |
| `GET /activity` | `PaginatedResponse[ActivityEvent]` | Paginated chronological timeline merging lifecycle events, task metrics, cost records, tool invocations, and delegation records (most recent first). Supports typed `ActivityEventType` enum filtering (invalid values return 400). Cost events are redacted for read-only roles. Response includes `degraded_sources` field for partial data detection |
| `GET /history` | `ApiResponse[tuple[CareerEvent, ...]]` | Career-relevant lifecycle events (hired, fired, promoted, demoted, onboarded) in chronological order |

The tracker is a LEDGER, not a judge. `TaskActivityObserver` writes one
`TaskMetricRecord` per terminal run, and the record's `quality_score` is the
completion oracle's own verdict, resolved from `completion_oracle_reports` at
write time. The oracle grades a deliverable against its own acceptance criteria
at the `IN_REVIEW` gate, strictly before the terminal transition, so the verdict
is already filed when the metric row is written.

That keeps "how good was this work" under a single owner. The tracker reads the
verdict and never derives one, so a task nobody reviewed carries no score at all
(`None`, read as unmeasured) rather than a fabricated number.

The translation is deterministic and lives in
`hr/performance/oracle_quality.py`: the verdict picks the band
(`approve` / `approve_with_notes` / `reject`), and each finding at or above HIGH
severity discounts within it, floored at zero. `escalate` resolves to `None`,
because no confident verdict was reached.

```yaml
agent_metrics:
  tasks_completed: 42
  tasks_failed: 2
  average_quality_score: 8.5     # mean completion-oracle verdict
  average_cost_per_task: 0.45
  average_completion_time: "2h"
```

???+ note "Design decisions ([Decision Log](../architecture/decisions.md) D11, D12)"

    **D11: Rolling Windows.** Pluggable `MetricsWindowStrategy` protocol. Initial
    strategy: multiple simultaneous windows:

    - **7d** for acute regressions
    - **30d** for sustained patterns
    - **90d** for baseline/drift

    Minimum 5 data points per window; below that, the system reports "insufficient data."
    Future strategies: fixed single window, per-metric configurable.

    ---

    **D12: Trend Detection.** Pluggable `TrendDetectionStrategy` protocol. Initial
    strategy: Theil-Sen regression slope per window + configurable thresholds classify
    trends as improving/stable/declining. Theil-Sen has 29.3% outlier breakdown (tolerates
    ~1 in 3 bad data points). Minimum 5 data points. Future strategies:
    period-over-period, OLS regression, threshold-only.

## Agent Evolution

A pluggable pipeline that turns execution outcomes into proposed changes to an
agent's prompt, its strategy preferences or its identity, each proposal passing
a guard chain before anything is applied. It follows the
[EvoSkill](https://arxiv.org/abs/2603.02766) three-agent separation principle:
the executing agent does not propose its own identity changes; a separate
analyser does.

The identity adapter ships **off**, so out of the box the pipeline can adjust
prompt injection and strategy preference and nothing else. Whether adaptation
improves an agent is not something the pipeline asserts; the rollback guard
watches the quality window afterwards and reverts on a regression.

### Architecture

```d2
Pipeline: "Evolution Pipeline" {
  Trigger: {
    T1: "BatchedTrigger\n(cron-like)"
    T2: "InflectionTrigger\n(performance trend)"
    T3: "PerTaskTrigger\n(post-execution)"
    T4: "CompositeTrigger\n(OR-combines)"
  }
  Context: "Build Context" {
    BC: "EvolutionContext\n(identity, performance, memories)"
  }
  Proposer: {
    P1: SeparateAnalyzerProposer
    P2: SelfReportProposer
    P3: CompositeProposer
  }
  Guards: {
    G1: RateLimitGuard
    G2: ReviewGateGuard
    G3: RollbackGuard
    G4: ShadowEvaluationGuard
    G5: CompositeGuard
  }
  Apply: "Adapter.apply" {
    A1: IdentityAdapter
    A2: StrategySelectionAdapter
    A3: PromptTemplateAdapter
  }

  Trigger -> Context -> Proposer -> Guards -> Apply
}
```

The pipeline is orchestrated by ``EvolutionService`` in ``engine/evolution/service.py``.

### Pluggable Axes

Every bullet is a strategy behind a ``@runtime_checkable Protocol``:

- **Triggers** (``engine/evolution/triggers/``): ``BatchedTrigger``, ``InflectionTrigger``, ``PerTaskTrigger``, ``CompositeTrigger``
- **Proposers** (``engine/evolution/proposers/``): ``SeparateAnalyzerProposer`` (EvoSkill strict), ``SelfReportProposer`` (heuristic), ``CompositeProposer`` (routes by outcome)
- **Adapters** (``engine/evolution/adapters/``): ``IdentityAdapter`` (identity mutation via version store), ``StrategySelectionAdapter`` (preference memory), ``PromptTemplateAdapter`` (prompt injection)
- **Guards** (``engine/evolution/guards/``): ``RateLimitGuard``, ``ReviewGateGuard``, ``RollbackGuard``, ``ShadowEvaluationGuard`` (runs adapted agent on a probe task suite via a pluggable ``ShadowTaskProvider`` + ``ShadowAgentRunner`` and rejects when score or pass rate regresses beyond configured tolerances), ``ApproveAllGuard`` (no-op fallback used when every real guard is disabled), ``CompositeGuard`` (chains ALL)

### Identity Version Store

``engine/identity/store/`` provides versioned identity storage with rollback:

- **``IdentityVersionStore``** protocol: ``put``, ``get_current``, ``get_version``, ``list_versions``, ``set_current`` (rollback)
- **``AppendOnlyIdentityStore``**: Every mutation appends a new version (full audit trail). ``set_current`` writes a new version pointing to the restored content.
- **``CopyOnWriteIdentityStore``**: Maintains a separate version pointer. ``set_current`` only updates the pointer (cheaper, but loses rollback audit trail).

Both wrap ``AgentRegistryService`` + ``VersioningService[AgentIdentity]``.

### Performance Inflection Events

``PerformanceTracker`` emits ``PerformanceInflection`` events via an ``InflectionSink`` protocol when a metric's trend direction changes (e.g., stable to declining). ``InflectionTrigger`` implements ``InflectionSink`` and queues events for the evolution service.

### Safe Defaults

| Axis | Default | Rationale |
|------|---------|-----------|
| Triggers | batched (daily) + inflection | Low cost, reactive |
| Proposer | composite (analyser for failures, self-report for success) | EvoSkill separation |
| Adapters | prompt_template ON, strategy_selection ON, identity OFF | Identity is highest risk |
| Guards | review_gate + rollback + rate_limit ON; shadow OFF | Safety first |
| Identity store | append_only | Audit trail by default |
| Propagation | none | Opt-in per org |

### Configuration

```yaml
evolution:
  enabled: true
  triggers:
    types: [batched, inflection]
    batched_interval_seconds: 86400
  proposer:
    type: composite
    model: example-basic-001
    temperature: 0.3
    max_tokens: 2000
  adapters:
    identity: false
    strategy_selection: true
    prompt_template: true
  guards:
    review_gate: true
    rollback: true
    rollback_window_tasks: 20
    rollback_regression_threshold: 0.1
    rate_limit: true
    rate_limit_per_day: 3
    shadow_evaluation: null        # null disables; set a ShadowEvaluationConfig to enable
  memory:
    capture:
      type: hybrid           # failure | success | hybrid
      min_quality_score: 8.0
    pruning:
      type: ttl              # ttl | pareto | hybrid
      max_age_days: 90
    propagation:
      type: none             # none | role_scoped | department_scoped
  identity_store:
    type: append_only
```

!!! note "Runtime wiring status"
    The evolution config, service, and factory are implemented and wired:
    ``build_evolution_service()`` is called from the worker engine assembly
    (``workers/engine_assembly.py``). Runtime evolution management has no REST
    API or dashboard UI; it is configured in the application code that wires the
    service.

---

## HR Service Layer

MCP handlers and REST controllers never reach into HR repositories directly; every read goes through a narrow service facade so auditing, pagination, and optional-dependency degradation stay in one place per domain. The services follow the standard protocol + strategy + factory + config-discriminator pattern where interchangeable backends exist (e.g. `AutonomyPolicyService`, `PruningService`), and collapse to a single class where the behaviour is strictly orchestration (e.g. `ActivityFeedService`).

| Service | Module | Role |
|---|---|---|
| `ActivityFeedService` | `src/synthorg/hr/activity_service.py` | Aggregates lifecycle events, task metrics, cost records, tool invocations, and delegation records into a single agent-scoped timeline for `synthorg_agents_get_activity`. Uses `asyncio.TaskGroup` with per-source safe-default helpers so one failing tracker cannot abort the merge. |
| `AgentHealthService` | `src/synthorg/hr/health/service.py` | Derives a compact `AgentHealthReport` (`healthy` / `degraded` / `unavailable`) from the tightest populated `PerformanceTracker` window. Rejects reports where `recent_failed_count > recent_task_count` via a cross-field validator. |
| `AgentVersionService` | `src/synthorg/hr/identity/version_service.py` | Reads paged identity-version history for `synthorg_agents_get_history`. Lifted out of the REST controller so the MCP surface doesn't depend on HTTP request/response shapes. |

## See Also

- [Agents](agents.md): agent identity, skills, identity versioning
- [Organisation](organization.md): company types, departments, templates
- [Budget & Cost](budget.md): performance-driven downgrade, risk budget
- [Design Overview](index.md): full index
