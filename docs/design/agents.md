---
title: Agents
description: Agent identity system. Personality dimensions, structured skill model, tool namespaces, runtime state, and identity versioning with audit trail.
---

# Agents

!!! warning "Designed behaviour; runtime in active development"

    This page is the source of truth for the **designed** behaviour of this subsystem. The autonomous agent runtime that exercises it end to end is in active development (see the [Roadmap](../roadmap/index.md)); the code described here is built and unit-tested as components but not yet run by a live agent.

Every agent is a composition of **immutable config** (identity, personality, skills, model, tool permissions, authority) and **mutable runtime state** (execution status, active task, cost accumulation). This page covers the identity layer. The HR lifecycle (seniority, hiring, firing, performance, evolution) lives on a dedicated [HR & Agent Lifecycle](hr-lifecycle.md) page.

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
    transitions, never mutated in place.

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

### Skill Model

Agent skills are represented as structured capability descriptions aligned with the
[A2A AgentSkill specification](communication-a2a.md#agent-card-projection), enabling lossless
bidirectional mapping between internal skills and external Agent Card capabilities.

```python
from pydantic import BaseModel, ConfigDict
from synthorg.core.types import NotBlankStr

class Skill(BaseModel):
    """Structured capability description, A2A AgentSkill-aligned."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr                              # e.g. "code-review"
    name: NotBlankStr                            # e.g. "Code Review"
    description: str = ""                        # human-readable capability description
    tags: tuple[NotBlankStr, ...] = ()           # searchable tags for multi-faceted matching
    input_modes: tuple[str, ...] = ("text/plain",)   # MIME types accepted
    output_modes: tuple[str, ...] = ("text/plain",)  # MIME types produced
    proficiency: float = 1.0                     # 0.0--1.0, agent's proficiency level

class SkillSet(BaseModel):
    """Agent skill inventory, split into primary and secondary."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    primary: tuple[Skill, ...] = ()
    secondary: tuple[Skill, ...] = ()
```

| Field | A2A AgentSkill Equivalent | Purpose |
|-------|--------------------------|---------|
| `id` | `id` | Unique skill identifier |
| `name` | `name` | Human-readable display name |
| `description` | `description` | Capability description for semantic matching |
| `tags` | `tags` | Searchable tags for multi-faceted routing |
| `input_modes` | `inputModes` | MIME types the agent accepts for this skill |
| `output_modes` | `outputModes` | MIME types the agent produces for this skill |
| `proficiency` | - | SynthOrg-specific: proficiency level for quality-aware routing |

**Defaults:**

- `input_modes` and `output_modes` default to `("text/plain",)`; internal agents that
  only handle text do not need to specify these fields
- `proficiency` defaults to `1.0`, only meaningful when comparing agents with the same
  skill at different proficiency levels
- `SkillSet` rejects string entries, duplicate skill IDs within a tier, and overlap
  between `primary` and `secondary` (pre-alpha, no compatibility coercion from any prior
  string-based shape)

**Routing impact:** `AgentTaskScorer` uses the structured skill data directly.  Primary
skill overlap is weighted at 40% and secondary at 20%, each contribution scaled by the
agent's `proficiency` for every matched skill (default `1.0`, which reproduces
boolean-match scoring).  When a subtask declares `required_tags`, matched skills whose
tags cover every required tag earn an additional 10% bonus.  Proficiency thus drives
quality-aware routing ("route to the agent with the highest Python proficiency") and
tags drive multi-faceted matching when callers opt in.

**Maintenance:** Skills will be template-seeded at hire time (company templates provide
default skill sets per role) and human-editable via the REST API. Auto-derivation from
task completion history is a planned future enhancement.

### Tool Namespaces

Tools are grouped by namespace and gated by `ToolPermission`:

| Namespace | Permission | Tools |
|-----------|-----------|-------|
| `communication.async_tasks` | `DELEGATION` | `start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks` |

The `communication.async_tasks` tools provide supervisor-facing async task
management wrapping `TaskEngine` (see [Async Delegation](communication-events.md#async-delegation)).

### Agent Configuration Example

???+ example "Full agent identity YAML"

    ```yaml
    # --- Config layer -- AgentIdentity (frozen) ---
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
        # Big Five (OCEAN-variant) -- internal scoring (0.0-1.0)
        openness: 0.4
        conscientiousness: 0.9
        extraversion: 0.3
        agreeableness: 0.5
        stress_response: 0.75
        # Behavioral enums -- injected into system prompts
        decision_making: "analytical"
        collaboration: "independent"
        verbosity: "balanced"
        conflict_approach: "compromise"
      skills:
        primary:
          - id: python
            name: Python
            description: "Backend development with Python 3.14+"
            tags: [backend, scripting]
            proficiency: 0.95
          - id: litestar
            name: Litestar
            description: "Async web framework API development"
            tags: [backend, api, async]
          - id: postgresql
            name: PostgreSQL
            description: "Relational database design and optimization"
            tags: [database, sql]
          - id: system-design
            name: System Design
            description: "Distributed system architecture"
            tags: [architecture, backend]
        secondary:
          - id: docker
            name: Docker
            tags: [devops, containers]
          - id: redis
            name: Redis
            tags: [database, caching]
          - id: testing
            name: Testing
            tags: [quality, automation]
      model:
        provider: "example-provider"
        model_id: "example-medium-001"
        temperature: 0.3
        max_tokens: 8192
        fallback_model: "openrouter/example-medium-001"
        model_tier: "medium"  # set by model matcher (large/medium/small/null)
      model_requirement:            # original tier/priority from template
        tier: "medium"
        priority: "balanced"
        min_context: 0
        capabilities: []
      memory:
        type: "persistent"       # persistent, project, session, none
        retention_days: null     # null = forever; also agent-level global default
        retention_overrides: []  # per-category overrides, e.g. [{category: "semantic", retention_days: 365}]
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
        # Progressive disclosure: list_tools, load_tool, and
        # load_tool_resource are always available regardless of
        # access_level.  L1 metadata is visible for all permitted
        # tools; L2/L3 content respects the same permission rules
        # as tool invocation.
      authority:
        can_approve: ["junior_dev_tasks", "code_reviews"]
        reports_to: "engineering_lead"
        can_delegate_to: ["junior_developers"]
        budget_limit: 5.00
      autonomy_level: null       # full, semi, supervised, locked (overrides defaults)
      strategic_output_mode: null  # option_expander, advisor, decision_maker, context_dependent (see strategy.md)
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
- **AgentRuntimeState** provides a lightweight per-agent execution status snapshot
  (idle / executing / paused) for dashboard queries and graceful-shutdown discovery.
  Persisted via `AgentStateRepository`, independent of the checkpoint system.

---

## Identity Versioning

`AgentRegistryService` creates ``VersionSnapshot[AgentIdentity]`` records for
``register()`` and ``update_identity()`` (charter/config changes such as model
swaps and level changes). ``update_status()`` (status transitions) is **not**
versioned: status changes are transient runtime state, not charter mutations.
This provides a full audit trail of charter changes and enables ``DecisionRecord``
entries to cite the exact charter version that was active during execution.

### Generic Infrastructure

The versioning system lives in `src/synthorg/versioning/` and is intentionally
entity-agnostic so it can be reused for other versioned entity types (tracked in
#1113):

- **`VersionSnapshot[T]`** (`versioning/models.py`): Generic frozen Pydantic model
  with fields `entity_id`, `version`, `content_hash`, `snapshot: T`, `saved_by`,
  `saved_at`. Version numbers are monotonically increasing per entity.
- **`compute_content_hash(model)`** (`versioning/hashing.py`): SHA-256 of
  `json.dumps(model.model_dump(mode="json"), sort_keys=True)`; stable across
  field-ordering variations in Pydantic serialization.
- **`VersioningService[T]`** (`versioning/service.py`): Wraps a `VersionRepository`
  to provide content-addressable snapshot creation. `snapshot_if_changed` skips the
  write when the content hash matches the latest stored version.
- **`VersionRepository[T]`** (`persistence/version_repo.py`): Generic protocol with
  `save_version` (idempotent INSERT OR IGNORE), `get_version`, `get_latest_version`,
  `get_by_content_hash`, `list_versions`, `count_versions`,
  `delete_versions_for_entity`.
- **`SQLiteVersionRepository[T]`** (`persistence/sqlite/version_repo.py`):
  Parameterized by `table_name`, `serialize_snapshot`, and `deserialize_snapshot`
  callables. Table name is validated at construction against
  `^[a-z][a-z0-9_]*$` to prevent SQL injection.

### Agent Identity Storage

Identity versions are persisted in the `agent_identity_versions` table (see
`schema.sql`). The `SQLitePersistenceBackend.identity_versions` property exposes a
pre-configured `SQLiteVersionRepository[AgentIdentity]`.

`AgentRegistryService` accepts an optional `VersioningService[AgentIdentity]`
dependency (constructor injection). The app factory (`api.app:create_app`) auto-wires
this dependency during startup so identity versioning is enabled out of the box;
no manual configuration required. When wired:

- `register()` snapshots the initial identity immediately after storing it.
- `update_identity()` snapshots the updated identity after applying the change.
- `evolve_identity()` snapshots the restored identity on rollback.
- All calls are best-effort: versioning failures are logged at WARNING and do not
  interrupt the registry mutation.

### REST API

Identity version history is exposed under `/api/v1/agents/{agent_id}/versions`
(paths in the table below are relative to that base):

| Method | Path (relative) | Guard | Description |
|--------|-----------------|-------|-------------|
| `GET` | `/` | read | Paginated list of version snapshots (`offset`, `limit` default 20) |
| `GET` | `/{version_num}` | read | Single version snapshot by monotonic version number |
| `GET` | `/diff?from_version=N&to_version=M` | read | Field-level `AgentIdentityDiff` between two versions (`from_version < to_version` required) |
| `POST` | `/rollback` | write | Restore a prior version.  Body: `{"target_version": <int>, "reason": "<text>"}` (reason optional).  Executed via `evolve_identity`, producing a new snapshot whose content hash equals the restored version; rollbacks never mutate history. |

All endpoints additionally verify that the stored snapshot's encoded owner id
matches the path `agent_id` (cross-agent rows are rejected with 400).

### Identity Diff

`src/synthorg/engine/identity/diff.py` provides identity-specific diff logic:

- **`IdentityFieldChange`**: A single field-level change with `field_path`
  (dot-notation, e.g. `personality.risk_tolerance`), `change_type`
  (`modified`/`added`/`removed`), and `old_value`/`new_value` (JSON strings).
- **`AgentIdentityDiff`**: Full diff summary with `agent_id`, `from_version`,
  `to_version`, `field_changes`, and a human-readable `summary`.
- **`compute_diff(agent_id, old, new, from_version, to_version)`**: Recursively
  compares `model_dump(mode="json")` output, descending into nested sub-models and
  dicts. Produces changes sorted by `field_path`.

### DecisionRecord Integration

When `ReviewGateService._record_decision` runs, it looks up the executing agent's
latest identity version from `persistence.identity_versions`. If found, it injects a
`charter_version` entry into the `DecisionRecord.metadata` dict:

```python
metadata = {
    "charter_version": {
        "agent_id": "...",
        "version": 3,
        "content_hash": "abc123...",
    }
}
```

This lookup is best-effort. On ``QueryError`` the decision record is written with
``{"charter_version_lookup_failed": True}`` in its metadata so operators can
distinguish lookup failures from the no-version-found case (where ``metadata``
is ``None``). The failure is logged at WARNING. No schema migration is required:
the ``metadata`` field on ``DecisionRecord`` was designed as a forward-compatible
extension point.

---

## See Also

- [HR & Agent Lifecycle](hr-lifecycle.md): seniority, hiring, firing, performance, evaluation, promotions, evolution, five-pillar framework, client agents
- [Organization](organization.md): company types, departments, templates
- [Tools & Capabilities](tools.md): tool access levels, progressive trust
- [Design Overview](index.md): full index
