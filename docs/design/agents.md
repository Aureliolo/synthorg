---
title: Agents
description: Agent identity. The bound (role, model) unit, structured skill model, tool namespaces, runtime state, and identity versioning with audit trail.
---

# Agents

An agent is the unit the build dispatches to. Its **role** decides who is offered
a piece of work and who is available to check one; its bound `(provider, model)`
pair decides what runs it, and the pair is fixed for as long as the agent exists,
so "which model produced this" has an answer after the fact.

Every agent is a composition of **immutable config** (identity, skills, model, tool permissions, authority) and **mutable runtime state** (execution status, active task, cost accumulation). This page covers the identity layer. The HR lifecycle (hiring, firing, performance, evolution) lives on a dedicated [HR & Agent Lifecycle](hr-lifecycle.md) page.

## Agent Identity Card

Every agent has a comprehensive identity. At the design level, agent data splits into three
layers:

Config (immutable)
:   Identity, skills, model preferences, tool permissions, and authority.
    Defined at hire time, changed only by explicit reconfiguration. Represented as frozen
    Pydantic models.

Runtime state (mutable-via-copy)
:   Current status, active task, conversation history, and execution metrics. Evolves during
    agent operation. Represented as Pydantic models using `model_copy(update=...)` for state
    transitions, never mutated in place.

Resolved provider view (never persisted)
:   What the agent's assigned model can actually do. Belongs to the provider, not the agent,
    so it is resolved per request at the API boundary and never written to agent config.
    See [Model capabilities on the wire](#model-capabilities-on-the-wire).

### Model capabilities on the wire

Two model-related fields ride on an agent, and they point in opposite directions:

`model_requirement` (input, config layer)
:   What the *role* demanded of a model when the matcher chose one: `priority`,
    `min_context`, `requires_vision`, `requires_reasoning`, and optionally a `family` or
    `model_pattern`. Persisted, round-trips through settings. Tool calling is deliberately
    absent, because the matcher applies it as a floor to every agent rather than as a
    per-role option.

`model_capabilities` (output, resolved per request)
:   What the *assigned model* can actually do, projected by
    `api/controllers/agents/_model_capabilities.py` from the provider's `ModelMetadata`:
    `supports_reasoning`, `supports_vision`, `tool_calling`, and `metadata_source`.

`AgentConfigResponse` lists the agent fields it exposes explicitly rather than inheriting from
`AgentConfig`, so a field added to the persisted schema reaches the wire only when someone
adds it here too, and a response can never be handed to a persistence path typed for
`AgentConfig`. The provider-level counterpart is `GET /providers/{name}/models` (see
[Providers](providers.md)); this is the agent-facing projection of the same metadata.

`tool_calling` is a named state rather than a boolean because "never observed" and "proven
incapable" are opposite facts that a truthiness check would conflate:

| Value | Meaning |
|---|---|
| `unverified` | No tool call has been observed yet. Not a fault. |
| `verified` | A real tool call succeeded. |
| `failed` | Repeated runtime failures proved the model cannot call tools. |

`model_capabilities` is `null` for two unrelated reasons, so `model_capability_status` names
which one applies. Without it a consumer reading the null alone cannot tell one agent
pointing at a deleted model from an entire org whose provider config momentarily could not
be read, and the dashboard would report the whole roster as broken during a settings outage.

| Value | Meaning |
|---|---|
| `resolved` | The binding resolved; `model_capabilities` is populated. |
| `unresolved` | The binding matches no configured model: unassigned, or stale after a removed model. |
| `provider_config_unavailable` | Provider config could not be read, so no binding was resolvable. Says nothing about this agent's binding. |

Reading provider config is failure-tolerant on every endpoint that projects capabilities
(`providers_for_capabilities`). Capabilities are derived display data layered onto an
operation with its own result: on a mutation the write has already committed by the time
they resolve, so a settings failure must not report a successful create or reorder as an
error and invite a duplicate retry. Tolerance covers any ordinary failure, not just
`SettingsError`, because an unwired resolver and a dropped store connection reach the
caller identically; only critical errors and cancellation still propagate.

For the same reason a mutation projects its response *before* publishing its WebSocket
event. Publishing cannot be retracted, so projecting afterwards would let a projection
failure fail the response while subscribers reload against a change the requester was
shown as an error.

The agent `id` is a stable UUID derived deterministically from the agent name
(`stable_agent_id(name)` = `uuid5(namespace, name)` in `core.types`). The config layer and the
runtime registry derive the same id independently from the name, so a config-sourced agent and
its registered `AgentIdentity` share one id without coordination. REST routes address agents by
this id (`/agents/{agent_id}`), matching the UUID that group chat addresses participants by.

### The Bound Unit

An agent is a fixed `(role, model)` pair. The role says what the agent is FOR, which is what
selection, routing and the decomposition planner reason about; the bound `(provider, model)`
says what actually runs it, and nothing in the loop re-points it (see
[No Bound-Pair Rewrite](providers.md)). How an agent's output READS is not a property of the
agent at all: it is governed centrally by
[Output Style Policy](output-style-policy.md), which applies the same house rules to every
agent and enforces the hard ones deterministically at the boundary where work is kept or sent.

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

**Routing impact:** `AgentTaskScorer` uses the structured skill data directly. Primary
skill overlap is weighted at 40% and secondary at 20%, each contribution scaled by the
agent's `proficiency` for every matched skill (default `1.0`, which reproduces
boolean-match scoring).  When a subtask declares `required_tags`, matched skills whose
tags cover every required tag earn an additional 10% bonus. Proficiency thus drives
quality-aware routing ("route to the agent with the highest Python proficiency") and
tags drive multi-faceted matching when callers opt in.

**Maintenance:** Skills are template-seeded at hire time (company templates provide
default skill sets per role) and human-editable via the REST API. Auto-derivation from
task completion history is not yet implemented.

### Tool Namespaces

Tools are grouped by namespace and gated by `ToolPermission`:

| Namespace | Permission | Tools |
|-----------|-----------|-------|
| `communication.async_tasks` | `DELEGATION` | `start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks` |

The `communication.async_tasks` tools provide supervisor-facing async task
management wrapping `TaskEngine` (see [Async Delegation](communication-events.md#async-delegation)).

### Agent Configuration Example

An agent is a bound `(role, model)` unit. How its output reads is governed
separately and deterministically; see
[Output-Style Policy](output-style-policy.md).

The example below is the whole agent record, which spans both layers rather
than matching either model exactly. `AgentConfig` is what an operator writes
and what `company.agents` persists; `AgentIdentity` is what `identity_from_config`
derives at bootstrap and what the engine runs. Both forbid extra keys, so each
field marked below belongs to one layer and is rejected by the other.

???+ example "Full agent record across both layers"

    ```yaml
    # --- Config layer: AgentConfig, plus the identity-only fields ---
    agent:
      # id is derived deterministically from name (uuid5); not user-set.
      id: "<derived-from-name>"
      name: "Sarah Chen"
      role: "Senior Backend Developer"
      department: "Engineering"
      skills:                     # AgentIdentity only
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
            description: "Relational database design and optimisation"
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
        model_id: "example-capable-001"
        temperature: 0.3
        max_tokens: 8192
        capability: "capable"  # derived by the matcher from the selected model's context window
      model_requirement:            # AgentConfig only: requirements from template
        priority: "balanced"        # quality / balanced / speed / cost
        min_context: 0
        requires_vision: false      # hard-require image input
        requires_reasoning: false   # hard-require extended reasoning
        family: null                # e.g. "example-expert": pin newest in family
        model_pattern: null         # e.g. "example-*": pin newest matching id
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
      hiring_date: "2026-02-27"  # AgentIdentity only
      status: "active"           # AgentIdentity only: active, on_leave, terminated
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
swaps and role changes). ``update_status()`` (status transitions) is **not**
versioned: status changes are transient runtime state, not charter mutations.
This provides a full audit trail of charter changes and enables ``DecisionRecord``
entries to cite the exact charter version that was active during execution.

### Generic Infrastructure

The versioning system lives in `src/synthorg/versioning/` and is intentionally
entity-agnostic so it can be reused for other versioned entity types:

- **`VersionSnapshot[T]`** (`versioning/models.py`): Generic frozen Pydantic model
  with fields `entity_id`, `version`, `content_hash`, `snapshot: T`, `saved_by`,
  `saved_at`. Version numbers are monotonically increasing per entity.
- **`compute_content_hash(model)`** (`versioning/hashing.py`): SHA-256 of
  `json.dumps(model.model_dump(mode="json"), sort_keys=True)`; stable across
  field-ordering variations in Pydantic serialisation.
- **`VersioningService[T]`** (`versioning/service.py`): Wraps a `VersionRepository`
  to provide content-addressable snapshot creation. `snapshot_if_changed` skips the
  write when the content hash matches the newest stored version.
- **`VersionRepository[T]`** (`persistence/version_protocol.py`): Generic protocol with
  `save_version` (idempotent INSERT OR IGNORE), `get_version`, `get_latest_version`,
  `get_by_content_hash`, `list_versions`, `count_versions`,
  `delete_versions_for_entity`.
- **`SQLiteVersionRepository[T]`** (`persistence/sqlite/version_repo.py`):
  Parameterised by `table_name`, `serialize_snapshot`, and `deserialize_snapshot`
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
- All calls are failure-tolerant: versioning failures are logged at WARNING and do not
  interrupt the registry mutation.

### REST API

Identity version history is exposed under `/api/v1/agents/{agent_id}/versions`
(paths in the table below are relative to that base):

| Method | Path (relative) | Guard | Description |
|--------|-----------------|-------|-------------|
| `GET` | `/` | read | Paginated list of version snapshots (`offset`, `limit` default 20) |
| `GET` | `/{version_num}` | read | Single version snapshot by monotonic version number |
| `GET` | `/diff?from_version=N&to_version=M` | read | Field-level `AgentIdentityDiff` between two versions (`from_version < to_version` required) |
| `POST` | `/rollback` | write | Restore a prior version. Body: `{"target_version": <int>, "reason": "<text>"}` (reason optional).  Executed via `evolve_identity`, producing a new snapshot whose content hash equals the restored version; rollbacks never mutate history. |

All endpoints additionally verify that the stored snapshot's encoded owner id
matches the path `agent_id` (cross-agent rows are rejected with 400).

### Identity Diff

`src/synthorg/engine/identity/diff.py` provides identity-specific diff logic:

- **`IdentityFieldChange`**: A single field-level change with `field_path`
  (dot-notation, e.g. `authority.budget_limit`), `change_type`
  (`modified`/`added`/`removed`), and `old_value`/`new_value` (JSON strings).
- **`AgentIdentityDiff`**: Full diff summary with `agent_id`, `from_version`,
  `to_version`, `field_changes`, and a human-readable `summary`.
- **`compute_diff(agent_id, old, new, from_version, to_version)`**: Recursively
  compares `model_dump(mode="json")` output, descending into nested sub-models and
  dicts. Produces changes sorted by `field_path`.

### DecisionRecord Integration

When `ReviewGateService._record_decision` runs, it looks up the executing agent's
newest identity version from `persistence.identity_versions`. If found, it injects a
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

This lookup is failure-tolerant. On ``QueryError`` the decision record is written with
``{"charter_version_lookup_failed": True}`` in its metadata so operators can
distinguish lookup failures from the no-version-found case (where ``metadata``
is ``None``). The failure is logged at WARNING. No schema migration is required:
the ``metadata`` field on ``DecisionRecord`` was designed as a forward-compatible
extension point.

---

## Built-in Roles

`BUILTIN_ROLES` in `src/synthorg/core/role_catalog.py` carries the
shipped role catalog. Every entry (CEO, Backend Developer, QA
Engineer, etc.) is a role definition only; it becomes a real
`AgentIdentity` when an operator staffs it. Two roles are distinguished
not by being special-cased but by what they do: they **judge** finished
work rather than performing it, and both live in Quality Assurance.

- **Completion Reviewer** (`name="Completion Reviewer"`, department:
  Quality Assurance). The independent peer reviewer of the completion
  oracle. A holder is selected per review when
  `engine.completion_oracle_enabled` is true (on by default); it reads a
  completing deliverable's acceptance criteria and its build and test
  evidence, and files a verdict. See
  [Verification & Quality: Completion Oracle Gate](verification-quality.md#completion-oracle-gate).
- **Red Team** (`name="Red Team"`, department: Quality Assurance).
  The adversarial sceptic. A holder is selected per evaluation when
  `CompanyConfig.security.red_team.enabled` is true, and runs as a gate
  before IN_REVIEW -> COMPLETED for deliverables whose `stakes` meet
  `engine.red_team_min_stakes` (default `high`). See
  [Security: Adversarial Red-Team Gate](security.md#adversarial-red-team-gate).

Both are ordinary staffable roles: an operator gives one to an agent
through the same role-assignment surface as any other, the holder appears
in the roster and in `GET /agents/active`, and its verdicts are comparable
per agent and per model like the rest of its work. Neither is
boot-instantiated; a synthetic identity for either is rejected by
`check_no_synthetic_agent_identity.py`, because a role nobody can be given
is authority nobody can see.

`core/role_catalog.py::role_is_gate_role` is the one declaration that a role
judges rather than performs, and three properties hang off it.

**The judge is selected per review, and is never the author.**
`hr/role_staffing.py` answers "who holds this role, and which of them fits this
work" for both gates, so there is one selection rule rather than two that drift.
Three independent things keep the judge off its own work: selection drops the
executor from the candidate pool before anything else happens; the gate refuses
a reviewer identity equal to the executor and escalates instead of reviewing,
which catches an identity it did not itself choose; and a row-level `CHECK` on
each verdict archive refuses any row naming one agent as both executor and
judge, so a self-review cannot be recorded even if both earlier layers were
bypassed.

Within the remaining pool, holders who already worked the reviewed initiative
are preferred, and widening org-wide is logged rather than silent. Capability
fit then decides: an exact rung, else the nearest higher, else the nearest
lower, logged as under-capability. The requirement is floored at the executor's
own rung, because the stakes and complexity that set the bar are proposed by the
agent that decomposed the work, and a bar with no floor lets the thing under
review bid its own judge down.

**The judging session is narrowed.** A roster agent carries whatever grants its
day job needs, and an injection planted in the artefact under review runs inside
the session reading it. `engine/review_session.py::as_review_session` therefore
dispatches a copy: STANDARD tool access, the verdict tool allowed by name, no
MCP capabilities, `EXTERNAL_DATA` withheld by category, SUPERVISED autonomy.
Identity, role, department and bound model are untouched, so the verdict is
still attributed to the real agent and still runs on the pair its operator
chose. This narrows the session for the duration of the dispatch, never the
roster.

**A gate role cannot own plan work.** A gate role is staffed, so it appears in
every roster read; but it judges rather than performs, so it is not something a
plan item can be owned by. `engine/decomposition/context.py::roster_from_agents`
is the single owner of "which roles may own a plan item" and filters gate roles
out of the list the planner is offered, because a planner offered one takes it,
and the party that judges then becomes the author of what it judges.

An org that staffs neither role does not silently skip review: the gate parks
the task and says which role is missing (see
[Nobody holds the role](verification-quality.md#nobody-holds-the-role)).
Every shipped company template staffs a Completion Reviewer for that
reason, and the security-hardened ones also staff a Red Team.

---

## See Also

- [HR & Agent Lifecycle](hr-lifecycle.md): role catalog, reporting-graph authority, hiring, firing, performance, evolution, client agents
- [Organisation](organization.md): company types, departments, templates
- [Tools & Capabilities](tools.md): tool access levels, sandboxing
- [Design Overview](index.md): full index
