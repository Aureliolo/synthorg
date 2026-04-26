---
title: Ceremony Scheduling
description: Pluggable ceremony scheduling strategies, velocity calculation, config resolution, and sprint lifecycle automation.
---

# Ceremony Scheduling

Sprint ceremonies bridge the sprint lifecycle with the meeting protocol system.
The ceremony scheduling subsystem provides a **pluggable strategy architecture**
that determines _when_ and _why_ ceremonies fire, how sprints auto-transition,
and how velocity is measured: all configurable per project, per department,
and per ceremony.

---

## Motivation

Synthetic agents operate orders of magnitude faster than human teams. A 14-day
sprint with bi-weekly retrospectives assumes human pace. Agents can finish an
entire sprint backlog in minutes, which means:

- **Velocity tracking breaks**: `story_points / duration_days` becomes
  meaningless when velocity is effectively infinite.
- **Ceremonies miss context**: A weekly standup fires long after the work it
  should reflect on is done.
- **Sprint boundaries create idle time**: Agents waiting for a calendar tick,
  or sprint boundaries are ignored entirely.
- **One size does not fit all**: A solo founder agent needs minimal ceremony
  overhead, while an enterprise template needs predictable cadence.

The ceremony scheduling system solves this by making the scheduling paradigm
itself pluggable. Users choose (or templates ship) the strategy that matches
their workflow rhythm.

---

## Design Decisions

**All scheduling paradigms are pluggable strategies.** The system ships eight
scheduling strategies (task-driven, calendar, hybrid, event-driven,
budget-driven, throughput-adaptive, external-trigger, milestone-driven)
behind a single `CeremonySchedulingStrategy` protocol. Users select the
strategy that fits their workflow at the project, department, or per-ceremony
level.

**Why pluggable:**

- Different templates naturally want different rhythms (startup vs. enterprise).
- The `CeremonySchedulingStrategy` protocol adds minimal implementation cost
  per strategy while maximizing flexibility.
- Users can switch strategies between sprints without any code changes.

**Key decisions:**

- **Templates can configure strategies at multiple levels.** The 3-level config
  resolution (project / department / per-ceremony) feeds into strategy
  selection, but the effective ``CeremonySchedulingStrategy`` is resolved and
  locked per sprint.
- **Each strategy defines its own velocity unit.** There is no forced
  normalization to points/sprint. Each strategy ships a default
  `VelocityCalculator` (e.g. task-driven uses `pts/task`, calendar uses
  `pts/day`). Users can override via settings.
- **Ceremony triggering integrates with coordination** via the protocol's
  lifecycle hooks (`on_task_completed`, `on_external_event`). The
  `CeremonyEvalContext` is extensible for coordination metadata. Detailed
  interaction patterns are deferred to the coordination integration phase.
- **Strategy is locked per-sprint.** Changes take effect at the next sprint
  start, with a migration notification to the responsible role.

---

## Architecture Overview

```text
TaskEngine  --[TaskStateChanged]--> CeremonyScheduler
                                         |
Sprint lifecycle <--[auto-transition]----+
                                         |
SprintCeremonyConfig --[bridge]--> MeetingTypeConfig
                                         |
                          MeetingScheduler.trigger_event()
```

The **CeremonyScheduler** is a coordination layer in `engine/workflow/` that:

1. Receives task completion events (and other lifecycle events) from the engine.
2. Delegates scheduling decisions to the active **CeremonySchedulingStrategy**.
3. Bridges ceremony configs into `MeetingTypeConfig` for the existing
   **MeetingScheduler** to execute.
4. Manages sprint auto-transitions when strategy-specific conditions are met.

The `CeremonyScheduler` does _not_ replace `MeetingScheduler`; it translates
sprint ceremony semantics into meeting system primitives.

---

## Scheduling Strategies

Eight pluggable strategies are available behind the `CeremonySchedulingStrategy`
protocol. Each strategy defines its own ceremony trigger logic, sprint
auto-transition behavior, and default velocity unit.

### Task-Driven

Ceremonies fire at task-count milestones. Sprints complete when tasks reach
terminal status, not on a timer. Natural fit for agent speed.

```yaml
sprint:
  ceremony_policy:
    strategy: task_driven
  ceremonies:
    - name: sprint_planning
      # fires on sprint_start (one-shot)
      policy_override:
        strategy_config:
          trigger: sprint_start
    - name: standup
      # fires every 5 task completions
      policy_override:
        strategy_config:
          trigger: every_n_completions
          every_n_completions: 5
    - name: retrospective
      # fires on sprint_end (all tasks done)
      policy_override:
        strategy_config:
          trigger: sprint_end
```

**Auto-transition**: ACTIVE to IN_REVIEW when task completion count reaches
the configured `transition_threshold` fraction of total tasks.

**Default velocity unit**: points per task (`pts/task`).

**Best for**: fast-moving agents, minimal overhead, solo founders, startups,
data pipelines.

### Calendar

Traditional time-based scheduling using `MeetingFrequency` intervals.
Ceremonies fire on a wall-clock cadence regardless of task progress.

```yaml
sprint:
  ceremony_policy:
    strategy: calendar
    strategy_config:
      duration_days: 14
  ceremonies:
    - name: standup
      frequency: daily
    - name: retrospective
      frequency: bi_weekly
```

**Auto-transition**: ACTIVE to IN_REVIEW at the `duration_days` boundary.
Task completion does _not_ trigger transition.

**Default velocity unit**: points per day (`pts/day`).

**Best for**: client-facing schedules, fixed reporting cadences, consultancies.

### Hybrid (First Wins)

Both calendar and task-driven triggers exist on each ceremony. Whichever fires
first wins and resets the cadence counter. Calendar provides a heartbeat floor;
task counts provide a throughput ceiling.

```yaml
sprint:
  ceremony_policy:
    strategy: hybrid
  ceremonies:
    - name: standup
      frequency: per_sprint_day        # calendar floor
      policy_override:
        strategy_config:
          every_n_completions: 10      # task ceiling
    - name: retrospective
      frequency: bi_weekly
      # also fires on sprint_end
```

**Auto-transition**: whichever comes first, task completion threshold _or_
calendar duration boundary.

**Default velocity unit**: points per sprint (`pts/sprint`).

**Best for**: teams that need predictability with throughput responsiveness,
dev shops, product teams, enterprises.

### Event-Driven

Ceremonies subscribe to engine events (`sprint_started`, `task_completed`,
`board_state_changed`, `sprint_backlog_empty`) with configurable debounce.
No fixed schedule; ceremonies fire reactively.

```yaml
sprint:
  ceremony_policy:
    strategy: event_driven
    strategy_config:
      debounce_default: 5
  ceremonies:
    - name: standup
      policy_override:
        strategy_config:
          on_event: task_completed
          debounce: 5                  # batch: fire once per 5 events
    - name: retrospective
      policy_override:
        strategy_config:
          on_event: sprint_backlog_empty
```

**Auto-transition**: on a configured event (e.g. `sprint_backlog_empty`).

**Default velocity unit**: points per sprint (`pts/sprint`).

**Best for**: flexible project-based work, agencies, composable with
the MessageBus.

### Budget-Driven

Ceremonies fire at cost-consumption thresholds. Ties directly into the
existing budget module (cost tracking, quota degradation, CFO optimization).

```yaml
sprint:
  ceremony_policy:
    strategy: budget_driven
    strategy_config:
      standup_at_budget_pct: [25, 50, 75]
      review_at_budget_pct: [50]
      retro_at_budget_pct: [100]
```

**Auto-transition**: when sprint budget is exhausted (100% consumed) or
when a configured budget threshold is crossed.

**Default velocity unit**: points per currency unit (`pts/<DEFAULT_CURRENCY>`, where `DEFAULT_CURRENCY` is defined in `src/synthorg/budget/currency.py` and can be overridden at runtime by the `budget.currency` setting).

**Best for**: cost-conscious organizations where every agent action has a
real dollar cost. Ensures ceremonies happen proportionally to spend.

### Throughput-Adaptive

Ceremonies fire when the team's throughput rate changes significantly.
Not "every N tasks" but "when pace anomaly detected." Like an automated
burndown chart monitor.

```yaml
sprint:
  ceremony_policy:
    strategy: throughput_adaptive
    strategy_config:
      velocity_drop_threshold_pct: 30    # standup when velocity drops 30%
      velocity_spike_threshold_pct: 50   # review when velocity spikes 50%
      measurement_window_tasks: 10       # rolling window for rate calc
```

**Auto-transition**: when task completion rate stabilizes after final tasks
complete (no anomaly-based transition; uses a completion threshold like
task-driven).

**Default velocity unit**: points per task (`pts/task`), with rate-of-change
as a secondary metric.

**Best for**: discovery/research work where pace varies wildly. Ceremonies
fire when something changes, not on a fixed schedule.

### External-Trigger

Ceremonies fire on external signals: webhooks, CI/CD events, git events,
MCP tool invocations. Bridges the synthetic org with real-world development
workflows.

```yaml
sprint:
  ceremony_policy:
    strategy: external_trigger
    strategy_config:
      sources:
        - type: webhook
          endpoint: /hooks/ceremony
        - type: git_event
          events: [push, tag]
  ceremonies:
    - name: code_review
      policy_override:
        strategy_config:
          on_external: pr_merged
    - name: retrospective
      policy_override:
        strategy_config:
          on_external: release_published
```

**Auto-transition**: on a configured external signal (e.g. `deploy_complete`).

**Default velocity unit**: points per sprint (`pts/sprint`).

**Best for**: integration with real-world dev workflows, CI/CD-driven
organizations.

### Milestone-Driven

Ceremonies fire at semantic project milestones rather than task counts or
time. Milestones are defined as tags on tasks or task groups.

```yaml
sprint:
  ceremony_policy:
    strategy: milestone_driven
    strategy_config:
      milestones:
        - name: feature_complete
          ceremony: sprint_review
        - name: code_freeze
          ceremony: retrospective
        - name: release_candidate
          ceremony: sprint_planning
```

**Auto-transition**: at a configured milestone (e.g. all tasks tagged
`release_candidate` are complete).

**Default velocity unit**: points per sprint (`pts/sprint`).

**Best for**: phased delivery, release-oriented workflows, open-source
projects with async contributors.


---

## Protocol Interfaces

### CeremonySchedulingStrategy

The primary pluggable interface. Implementations provide stateless evaluation
methods plus optional lifecycle hooks for stateful strategies.

```python
@runtime_checkable
class CeremonySchedulingStrategy(Protocol):
    """Pluggable strategy for ceremony scheduling."""

    # Core evaluation (stateless, called per event)

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,
        context: CeremonyEvalContext,
    ) -> bool:
        """Evaluate whether a ceremony should fire right now."""
        ...

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Return target status if sprint should auto-transition, else None."""
        ...

    # Lifecycle hooks (optional, for stateful strategies)

    async def on_sprint_activated(
        self, sprint: Sprint, config: SprintConfig,
    ) -> None: ...

    async def on_sprint_deactivated(self) -> None: ...

    async def on_task_completed(
        self, sprint: Sprint, task_id: str, story_points: float,
        context: CeremonyEvalContext,
    ) -> None: ...

    async def on_task_added(
        self, sprint: Sprint, task_id: str,
    ) -> None: ...

    async def on_task_blocked(
        self, sprint: Sprint, task_id: str,
    ) -> None: ...

    async def on_budget_updated(
        self, sprint: Sprint, budget_consumed_fraction: float,
    ) -> None: ...

    async def on_external_event(
        self, sprint: Sprint, event_name: str,
        payload: Mapping[str, Any],
    ) -> None: ...

    # Metadata

    @property
    def strategy_type(self) -> CeremonyStrategyType: ...

    def get_default_velocity_calculator(self) -> VelocityCalcType: ...

    def validate_strategy_config(
        self, config: Mapping[str, Any],
    ) -> None: ...
```

**Design rationale**: The protocol is intentionally feature-rich with many
hooks. Simple strategies (task-driven, calendar) implement no-op hooks.
Complex strategies (external-trigger, throughput-adaptive) use hooks to
subscribe to events or track rate metrics. This ensures future strategies
have maximum flexibility without protocol changes.

### VelocityCalculator

Pluggable velocity computation. Each strategy ships a default calculator,
but users can override.

```python
@runtime_checkable
class VelocityCalculator(Protocol):
    """Pluggable velocity computation."""

    def compute(self, record: VelocityRecord) -> VelocityMetrics:
        """Compute velocity metrics from a single sprint record."""
        ...

    def rolling_average(
        self, records: Sequence[VelocityRecord], window: int,
    ) -> VelocityMetrics:
        """Compute rolling average over recent sprints."""
        ...

    @property
    def calculator_type(self) -> VelocityCalcType: ...

    @property
    def primary_unit(self) -> str: ...
```

**Shipped implementations:**

| Calculator | Primary unit | Strategy default for |
|------------|-------------|---------------------|
| `TaskDrivenVelocityCalculator` | `pts/task` | task_driven, throughput_adaptive |
| `CalendarVelocityCalculator` | `pts/day` | calendar |
| `MultiDimensionalVelocityCalculator` | `pts/sprint` (+ secondary) | hybrid |
| `BudgetVelocityCalculator` | `pts/<DEFAULT_CURRENCY>` | budget_driven |
| `PointsPerSprintVelocityCalculator` | `pts/sprint` | event_driven, external_trigger, milestone_driven |

---

## Configuration

### CeremonyPolicyConfig

A unified policy model that bundles strategy, velocity, and transition
settings. Appears at three levels for hierarchical override resolution.

```python
class CeremonyPolicyConfig(BaseModel):
    """Ceremony scheduling policy."""
    strategy: CeremonyStrategyType | None = None
    strategy_config: Mapping[str, Any] | None = None
    velocity_calculator: VelocityCalcType | None = None
    auto_transition: bool | None = None
    transition_threshold: float | None = None   # (0.0, 1.0]
```

### 3-Level Resolution

```text
ceremony.policy_override  ??  department.ceremony_policy  ??  sprint_config.ceremony_policy
       (most specific)            (department scope)              (project default)
```

Field-by-field resolution via `resolve_ceremony_policy()`: each field is
resolved independently from the most specific level that provides a non-None
value. If no level provides a value, the framework default applies.

**Example:**

```yaml
# Project default: task-driven, auto-transition at 100%
workflow:
  sprint:
    ceremony_policy:
      strategy: task_driven
      auto_transition: true
      transition_threshold: 1.0

departments:
  marketing:
    # Override: calendar for marketing
    ceremony_policy:
      strategy: calendar

  engineering:
    # Inherits task_driven from project default

ceremonies:
  - name: standup
    # Inherits department/project strategy
  - name: retrospective
    policy_override:
      # Per-ceremony: always event-driven for retros
      strategy: event_driven
```

Resolution for marketing's standup: `calendar` (from department).
Resolution for engineering's standup: `task_driven` (from project).
Resolution for any department's retrospective: `event_driven` (per-ceremony).

### Runtime Mutability

Strategies are **locked per sprint**. Changes take effect at the next sprint
start. This prevents confusing mid-sprint behavior changes.

When a strategy change is pending:

1. A **migration notification** is sent to the responsible role (scrum master,
   department head, or whoever manages the task list).
2. A **warning** is displayed: the change will take effect at the next sprint
   start and may cause initial optimization issues in the first few sprints
   until the new cadence stabilizes.
3. The responsible role may need to reorder or reorganize the backlog for
   the new system.

---

## CeremonyScheduler Service

The `CeremonyScheduler` is a runtime coordination service that:

- **Owns ceremony trigger state**: completion counters, fired-once tracking,
  activation timestamps. This state is ephemeral (resets per sprint, not
  persisted).
- **Delegates decisions**: calls `strategy.should_fire_ceremony()` and
  `strategy.should_transition_sprint()` on events.
- **Bridges to meetings**: converts ceremony triggers into
  `MeetingScheduler.trigger_event()` calls.
- **Returns transitioned sprints**: follows the immutable pattern; returns
  a new `Sprint` instance if auto-transition occurred.

```python
class CeremonyScheduler:
    async def activate_sprint(
        self, sprint: Sprint, config: SprintConfig,
        strategy: CeremonySchedulingStrategy,
        *, velocity_history: tuple[VelocityRecord, ...] = (),
    ) -> StrategyMigrationInfo | None: ...

    async def deactivate_sprint(self) -> None: ...

    async def on_task_completed(
        self, sprint: Sprint, task_id: str, story_points: float,
    ) -> Sprint: ...
```

### Ceremony-to-Meeting Bridge

Pure functions convert `SprintCeremonyConfig` into `MeetingTypeConfig`:

- Frequency-based ceremonies map to periodic `MeetingTypeConfig`.
- Trigger-based ceremonies map to event-triggered `MeetingTypeConfig`
  with deterministic event names: `ceremony.<name>.<sprint_id>`.
- Protocol type, token budget, and participants carry through.

---

## Velocity Tracking

### VelocityRecord Extensions

`VelocityRecord` captures all available dimensions regardless of strategy:

| Field | Type | Description |
|-------|------|-------------|
| `sprint_id` | `NotBlankStr` | Reference to completed sprint |
| `sprint_number` | `int` | Sequential sprint number |
| `story_points_committed` | `float` | Points planned |
| `story_points_completed` | `float` | Points delivered |
| `duration_days` | `int` | Sprint duration in calendar days |
| `task_completion_count` | `int \| None` | Tasks completed (new) |
| `wall_clock_seconds` | `float \| None` | Real elapsed time (new) |
| `budget_consumed` | `float \| None` | Cost consumed (new) |

Strategies populate what applies. `None` fields indicate the dimension is
not tracked by the active strategy.

### Strategy Velocity Defaults

Each strategy declares a default `VelocityCalcType`. Users can override
via `CeremonyPolicyConfig.velocity_calculator`.

| Strategy | Default velocity | Unit |
|----------|-----------------|------|
| task_driven | `TASK_DRIVEN` | pts/task |
| calendar | `CALENDAR` | pts/day |
| hybrid | `MULTI_DIMENSIONAL` | pts/sprint + secondary |
| event_driven | `POINTS_PER_SPRINT` | pts/sprint |
| budget_driven | `BUDGET` | pts/&lt;DEFAULT_CURRENCY&gt; |
| throughput_adaptive | `TASK_DRIVEN` | pts/task + rate |
| external_trigger | `POINTS_PER_SPRINT` | pts/sprint |
| milestone_driven | `POINTS_PER_SPRINT` | pts/sprint |

### Strategy Change and Velocity Window

When the ceremony strategy changes between sprints:

- The velocity rolling-average window **resets** (previous records used a
  different unit/calculation).
- A strategy change is logged at INFO level using the
  `SPRINT_CEREMONY_STRATEGY_CHANGED` event.  The velocity
  rolling-average window resets; prior records are preserved but
  their computed metrics no longer carry over.
- Historical `VelocityRecord` data is preserved (raw dimensions are always
  stored). Only the _computed_ velocity resets.

---

## Template Defaults

Each built-in template ships with a default ceremony strategy that matches
its persona. Users can override via settings.

| Template | Default strategy | Rationale |
|----------|-----------------|-----------|
| `solo_founder` | `task_driven` | 1 agent, minimal ceremony overhead |
| `startup` | `task_driven` | Speed matters, iterate fast |
| `dev_shop` | `hybrid` | Client deadlines + throughput responsiveness |
| `product_team` | `hybrid` | Sprint cadence + task responsiveness |
| `agency` | `event_driven` | Deliverable-oriented, project-based |
| `full_company` | `hybrid` | Enterprise predictability + throughput |
| `research_lab` | `throughput_adaptive` | Discovery pace varies wildly |
| `consultancy` | `calendar` | Client expects fixed reporting schedules |
| `data_team` | `task_driven` | Pipeline/batch completion oriented |

---

## Observability

Event constants in `synthorg.observability.events.workflow`:

| Event | Description |
|-------|-------------|
| `SPRINT_CEREMONY_SCHEDULED` | Ceremony scheduled (existing) |
| `SPRINT_CEREMONY_TRIGGERED` | Ceremony triggered by strategy evaluation |
| `SPRINT_CEREMONY_SKIPPED` | Ceremony evaluation returned false |
| `SPRINT_AUTO_TRANSITION` | Sprint auto-transitioned by strategy |
| `SPRINT_CEREMONY_SCHEDULER_STARTED` | CeremonyScheduler activated for sprint |
| `SPRINT_CEREMONY_SCHEDULER_STOPPED` | CeremonyScheduler deactivated |
| `SPRINT_CEREMONY_BRIDGE_CREATED` | Ceremony config bridged to meeting type |
| `SPRINT_CEREMONY_POLICY_RESOLVED` | 3-level policy resolution completed |
| `SPRINT_CEREMONY_STRATEGY_CHANGED` | Strategy change detected between sprints |
| `VELOCITY_TASK_DRIVEN_NO_TASK_COUNT` | VelocityRecord has no task_completion_count for task-driven calculation |
| `VELOCITY_CALENDAR_NO_DURATION` | CalendarVelocityCalculator received a record with zero duration_days (defensive) |
| `VELOCITY_MULTI_NO_TASK_COUNT` | MultiDimensionalVelocityCalculator: no task_completion_count |
| `VELOCITY_MULTI_NO_DURATION` | MultiDimensionalVelocityCalculator received a record with zero duration_days (defensive) |
| `SPRINT_CEREMONY_EVENT_DEBOUNCE_NOT_MET` | Event-driven strategy debounce threshold not yet met |
| `SPRINT_CEREMONY_EVENT_COUNTER_INCREMENTED` | Event-driven strategy incremented an internal event counter |
| `SPRINT_CEREMONY_BUDGET_THRESHOLD_CROSSED` | Budget-driven strategy detected a budget threshold crossing |
| `SPRINT_CEREMONY_BUDGET_THRESHOLD_ALREADY_FIRED` | Budget-driven strategy skipped an already-fired threshold |
| `SPRINT_AUTO_TRANSITION_BUDGET` | Sprint auto-transitioned due to budget exhaustion |
| `VELOCITY_BUDGET_NO_BUDGET_CONSUMED` | BudgetVelocityCalculator: record has None or zero budget_consumed |
| `SPRINT_CEREMONY_THROUGHPUT_BASELINE_SET` | Throughput-adaptive strategy established baseline rate |
| `SPRINT_CEREMONY_THROUGHPUT_DROP_DETECTED` | Throughput-adaptive strategy detected a velocity drop |
| `SPRINT_CEREMONY_THROUGHPUT_SPIKE_DETECTED` | Throughput-adaptive strategy detected a velocity spike |
| `SPRINT_CEREMONY_THROUGHPUT_COLD_START` | Throughput-adaptive strategy in cold start (baseline not yet established) |
| `SPRINT_CEREMONY_EXTERNAL_EVENT_RECEIVED` | External-trigger strategy received an external event |
| `SPRINT_CEREMONY_EXTERNAL_EVENT_MATCHED` | External-trigger strategy matched an external event to a ceremony |
| `SPRINT_CEREMONY_EXTERNAL_SOURCE_REGISTERED` | External-trigger strategy registered event sources |
| `SPRINT_CEREMONY_EXTERNAL_SOURCE_CLEARED` | External-trigger strategy cleared event sources |
| `SPRINT_CEREMONY_MILESTONE_ASSIGNED` | Task assigned to a milestone |
| `SPRINT_CEREMONY_MILESTONE_UNASSIGNED` | Task removed from a milestone |
| `SPRINT_CEREMONY_MILESTONE_COMPLETED` | All tasks in a milestone are complete |
| `SPRINT_CEREMONY_MILESTONE_NOT_READY` | Milestone has no tasks assigned; cannot fire |
| `SPRINT_AUTO_TRANSITION_MILESTONE` | Sprint auto-transitioned at a milestone completion |

---

## Implementation Roadmap

### Shipped in #961 (Foundation)

- `CeremonySchedulingStrategy` protocol (all hooks, maximally extensible)
- `VelocityCalculator` protocol
- `CeremonyStrategyType` enum (all 8 members)
- `CeremonyPolicyConfig` + `ResolvedCeremonyPolicy` + `resolve_ceremony_policy()`
- `CeremonyEvalContext` (rich evaluation context)
- `CeremonyScheduler` service
- Ceremony-to-meeting bridge functions
- `TaskDrivenStrategy` reference implementation
- `TaskDrivenVelocityCalculator` reference implementation
- `VelocityCalcType` enum, `VelocityMetrics` model
- `VelocityRecord` extensions (task_completion_count, wall_clock_seconds, budget_consumed)
- `SprintConfig` + `SprintCeremonyConfig` extensions (ceremony_policy, policy_override)
- Observability event constants
- This design page

### Shipped in #969 + #970 (Calendar + Hybrid Strategies)

- `CalendarStrategy`: time-based ceremony firing using `MeetingFrequency` intervals
- `CalendarVelocityCalculator`: `pts/day` with duration-weighted rolling averages
- `HybridStrategy`: first-wins between calendar (floor) and task-driven (ceiling) triggers
- `MultiDimensionalVelocityCalculator`: `pts/sprint` with `pts_per_task`, `pts_per_day`, `completion_ratio` secondaries
- Observability event constants (`VELOCITY_CALENDAR_NO_DURATION`, defensive, for invalid/unvalidated records only; `VELOCITY_MULTI_NO_TASK_COUNT`, `VELOCITY_MULTI_NO_DURATION`, defensive, for invalid/unvalidated records only)

### Implemented in #971 + #972 (Event-Driven + Budget-Driven Strategies, integration pending)

- `EventDrivenStrategy`: reactive ceremony firing on engine events with configurable debounce
- `PointsPerSprintVelocityCalculator`: `pts/sprint` for event-driven and external-trigger strategies
- `BudgetDrivenStrategy`: ceremony firing at cost-consumption thresholds
- `BudgetVelocityCalculator`: `pts/<DEFAULT_CURRENCY>` with budget-weighted rolling averages
- Observability event constants (`SPRINT_CEREMONY_EVENT_DEBOUNCE_NOT_MET`, `SPRINT_CEREMONY_EVENT_COUNTER_INCREMENTED`, `SPRINT_CEREMONY_BUDGET_THRESHOLD_CROSSED`, `SPRINT_CEREMONY_BUDGET_THRESHOLD_ALREADY_FIRED`, `SPRINT_AUTO_TRANSITION_BUDGET`, `VELOCITY_BUDGET_NO_BUDGET_CONSUMED`)

> **Note:** The `CeremonyScheduler` does not yet wire all lifecycle hooks (`on_task_added`, `on_task_blocked`, `on_budget_updated`, `on_external_event`). Until scheduler integration is complete, these strategies' event counters will not increment for those event types. Scheduler integration is tracked in follow-up work.

### Implemented in #973 + #974 (Throughput-Adaptive + External-Trigger Strategies, integration pending)

- `ThroughputAdaptiveStrategy`: ceremony firing when rolling throughput rate drops or spikes beyond configured thresholds (velocity_drop_threshold_pct, velocity_spike_threshold_pct, measurement_window_tasks). Baseline rate established from first full window, frozen per sprint. Default velocity calculator: `TaskDrivenVelocityCalculator`.
- `ExternalTriggerStrategy`: ceremony firing on named external signals (webhooks, git events, MCP invocations). Event matching via `context.external_events` and `on_external_event` lifecycle hook buffer. Source registration is declarative. Default velocity calculator: `PointsPerSprintVelocityCalculator`.
- Observability event constants (`SPRINT_CEREMONY_THROUGHPUT_BASELINE_SET`, `SPRINT_CEREMONY_THROUGHPUT_DROP_DETECTED`, `SPRINT_CEREMONY_THROUGHPUT_SPIKE_DETECTED`, `SPRINT_CEREMONY_THROUGHPUT_COLD_START`, `SPRINT_CEREMONY_EXTERNAL_EVENT_RECEIVED`, `SPRINT_CEREMONY_EXTERNAL_EVENT_MATCHED`, `SPRINT_CEREMONY_EXTERNAL_SOURCE_REGISTERED`, `SPRINT_CEREMONY_EXTERNAL_SOURCE_CLEARED`)

> **Note:** Like #971 + #972, the `CeremonyScheduler` does not yet wire all lifecycle hooks for these strategies. Scheduler integration is tracked in follow-up work.

### Shipped in #975 + #976 + #980 (Milestone-Driven + Template Defaults + Department Overrides)

- `MilestoneDrivenStrategy`: ceremony firing when all tasks tagged with a milestone are complete. Milestone-to-task assignment via `on_external_event` (`milestone_assign`/`milestone_unassign`). Edge-triggered (each milestone fires exactly once per sprint). Configurable `transition_milestone` for sprint auto-transition. Default velocity calculator: `PointsPerSprintVelocityCalculator`.
- Template default ceremony strategy assignments for all 9 builtins (solo_founder/startup/data_team: task_driven, dev_shop/product_team/full_company: hybrid, agency: event_driven, research_lab: throughput_adaptive, consultancy: calendar).
- Per-department `ceremony_policy` override in `TemplateDepartmentConfig` and `Department` models. Department-level overrides merge field-by-field with project default via `resolve_ceremony_policy()`.
- Observability event constants (`SPRINT_CEREMONY_MILESTONE_ASSIGNED`, `SPRINT_CEREMONY_MILESTONE_UNASSIGNED`, `SPRINT_CEREMONY_MILESTONE_COMPLETED`, `SPRINT_CEREMONY_MILESTONE_NOT_READY`, `SPRINT_AUTO_TRANSITION_MILESTONE`)

> **Note:** Like #973 + #974, the `CeremonyScheduler` does not yet wire all lifecycle hooks for the milestone-driven strategy. Scheduler integration is tracked in follow-up work.

### Shipped in #978 (Strategy Migration UX)

- `StrategyMigrationInfo` frozen model (sprint_id, previous/new strategy, velocity_history_size)
- `detect_strategy_migration()` pure function for change detection
- `format_migration_warning()` and `format_reorder_prompt()` pure text formatters
- `notify_strategy_migration()` async best-effort notification via `AgentMessenger`
- `CeremonyScheduler.activate_sprint()` captures previous strategy type before deactivation, returns `StrategyMigrationInfo | None`
- `SPRINT_CEREMONY_STRATEGY_CHANGED` event logged on detection
- Strategy-aware velocity calculator default in `resolve_ceremony_policy()` (`STRATEGY_DEFAULT_VELOCITY_CALC` mapping)
- Explicit `velocity_calculator` in all 9 builtin template ceremony policies

### Shipped in #979 (Dashboard UI)

- Ceremony policy API controller: project-level policy query, resolved policy with
  field-level origin tracking, active strategy query
- Department ceremony-policy endpoints: GET/PUT/DELETE per-department overrides in
  departments controller
- 7 ceremony policy settings in coordination namespace (ceremony_strategy,
  ceremony_strategy_config, ceremony_velocity_calculator, ceremony_auto_transition,
  ceremony_transition_threshold, dept_ceremony_policies, ceremony_policy_overrides)
- Ceremony policy settings page at ``/settings/coordination/ceremony-policy`` with
  project policy editor, department overrides panel, per-ceremony overrides panel
- Shared UI components: PolicySourceBadge (field origin indicator), InheritToggle
  (inherit/override switch)
