---
title: Semantic Ontology
description: Shared entity vocabulary, versioned definitions, drift detection, and context injection for inter-agent semantic alignment.
---

# Semantic Ontology

!!! warning "Designed behaviour; runtime in active development"

    This page is the source of truth for the **designed** behaviour of this subsystem. The autonomous agent runtime that exercises it end to end is in active development (see the [Roadmap](../roadmap/index.md)); the code described here is built and unit-tested as components but not yet run by a live agent.

The ontology subsystem provides a shared, versioned vocabulary of entity
definitions that agents use to communicate unambiguously. Every core concept
(Task, Agent, Role, etc.) has a canonical definition registered at startup,
ensuring all agents share the same understanding of domain terminology.

---

## Architecture

```d2
direction: down

decorator: "@ontology_entity\n(decorator)"
config: "YAML config\n(entities: section)"

decorator_registry: "Decorator Registry"
config_loader: "Config Loader\n(EntitiesConfig)"

decorator -> decorator_registry
config -> config_loader

ontology_service: "OntologyService\n(orchestrator)"

decorator_registry -> ontology_service
config_loader -> ontology_service

ontology_backend: "OntologyBackend\n(protocol)"
versioning_service: "VersioningService\n[EntityDefinition]"

ontology_service -> ontology_backend
ontology_service -> versioning_service

sqlite_backend: "SQLite Backend\n(entity_defs)"
sqlite_version_repo: "SQLiteVersionRepo\n(entity_def_vers)"

ontology_backend -> sqlite_backend
versioning_service -> sqlite_version_repo

same_db: "same DB"
sqlite_backend -> same_db
sqlite_version_repo -> same_db
```

## Entity Definitions

Each entity definition contains:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `NotBlankStr` | Unique entity name (e.g. `Task`, `AgentIdentity`) |
| `tier` | `EntityTier` | `CORE` (framework-protected) or `USER` (domain-specific) |
| `source` | `EntitySource` | `AUTO` (decorator), `CONFIG` (YAML), or `API` (REST) |
| `definition` | `str` | Free-text description of the entity |
| `fields` | `tuple[EntityField, ...]` | Typed field descriptors with descriptions |
| `constraints` | `tuple[str, ...]` | Business rule descriptions |
| `disambiguation` | `str` | What this entity is *not* |
| `relationships` | `tuple[EntityRelation, ...]` | Relationships to other entities |

## `@ontology_entity` Decorator

Applied to Pydantic models to auto-derive entity definitions:

```python
@ontology_entity
class Task(BaseModel):
    """A unit of work within the company."""

    title: str = Field(description="Task title")
    status: TaskStatus = Field(description="Current status")
```

The decorator inspects the model's docstring and `Field(description=...)`
annotations.  Registration is lazy; the decorator stores a reference to
the class, and `EntityDefinition` objects are built on first access via
`get_entity_registry()`.

## Core Entities

The following 12 models are registered at startup:

| Entity | Model | Source |
|--------|-------|--------|
| Task | `core.task.Task` | AUTO |
| AgentIdentity | `core.agent.AgentIdentity` | AUTO |
| Role | `core.role.Role` | AUTO |
| Department | `core.company.Department` | AUTO |
| Project | `core.project.Project` | AUTO |
| Artifact | `core.artifact.Artifact` | AUTO |
| Approval | `core.approval.ApprovalItem` | AUTO |
| CostRecord | `budget.cost_record.CostRecord` | AUTO |
| Message | `communication.message.Message` | AUTO |
| Meeting | `communication.meeting.models.MeetingRecord` | AUTO |
| OrgFact | `memory.org.models.OrgFact` | AUTO |
| DecisionRecord | `engine.decisions.DecisionRecord` | AUTO |

## Versioning

Entity definitions are versioned using the existing `VersioningService[T]`
pattern.  Content-addressable SHA-256 hashing deduplicates unchanged
definitions.  Version table: `entity_definition_versions` (same schema as
`agent_identity_versions`).

## Configuration

```yaml
ontology:
  backend: "sqlite"                    # Backend selection
  injection:
    strategy: "hybrid"                 # hybrid | full | summary | none
    core_token_budget: 2000
    tool_name: "get_entity_definition"
  drift_detection:
    strategy: "passive"                # passive | active | none
    check_interval: 300
    threshold: 0.3
  delegation_guard:
    guard_mode: "stamp"                # none | stamp | validate | enforce
  memory:
    wrapper_enabled: true
    auto_tag: true
    warn_on_drift: true
  sync:
    org_memory_enabled: true
  entities:                            # User-defined entities
    entries:
      - name: "Invoice"
        definition: "A financial document."
        fields:
          amount: "Total amount"
          currency: "Currency code"
```

## Drift Detection

The `DriftReport` model captures divergence between an agent's usage of a
concept and the canonical definition:

- `divergence_score`: 0.0 (aligned) to 1.0 (fully divergent)
- `divergent_agents`: per-agent detail with individual scores
- `recommendation`: `NO_ACTION`, `NOTIFY`, `RETRAIN`, or `ESCALATE`

## Bootstrap Sequence

1. `SQLiteOntologyBackend` connects and applies schema
2. `VersioningService[EntityDefinition]` is created from the backend's DB
3. `OntologyService.bootstrap()` registers all `@ontology_entity` models
4. `OntologyService.bootstrap_from_config()` registers YAML entities
5. Service is available for CRUD operations and version queries

## Error Hierarchy

```text
OntologyError (base)
  +-- OntologyConnectionError
  +-- OntologyNotFoundError
  +-- OntologyDuplicateError
  +-- OntologyConfigError
```
