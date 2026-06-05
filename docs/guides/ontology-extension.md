---
title: Ontology Extension
description: Register a new entity definition and observe how it threads into agent context.
---

# Ontology Extension

SynthOrg's semantic ontology lives at `synthorg.ontology`. It holds the
canonical definitions of the concepts the platform reasons about, such as
`Task`, `Project`, `Department`, and `Artifact`. Each definition pairs a
free-text description with typed fields, constraints, disambiguation text,
and relationships. The injection layer threads these definitions into agent
context so every agent shares one consistent vocabulary. This guide covers
the two ways to add an entity and shows how a definition reaches an agent.

## Concepts

- **Entity definition**: an [`EntityDefinition`][def] value object with a
  `name`, a `definition`, typed `fields`, `constraints`, `disambiguation`,
  and `relationships`. Definitions are immutable; updates create new
  versions through the versioning service.
- **Tier**: `EntityTier.CORE` for framework-provided definitions (protected
  from user edits) or `EntityTier.USER` for domain definitions you add.
- **Source**: how the definition entered the ontology. `AUTO` (the
  `@ontology_entity` decorator), `CONFIG` (a company template), or `API`
  (the REST endpoint).
- **Injection strategy**: how definitions reach an agent. The
  `OntologyInjectionConfig.strategy` setting selects `prompt`, `tool`,
  `hybrid` (the default), or `memory`.

[def]: ../design/ontology.md

## Option A: a framework entity via `@ontology_entity`

Decorate any Pydantic `BaseModel` with `@ontology_entity`. The decorator
registers the class in a module-level registry; `OntologyService.bootstrap()`
later derives an `EntityDefinition` from it. The class docstring becomes the
`definition`, and every field that carries a `Field(description=...)` becomes
an `EntityField`. The default tier is `CORE` and the default source is `AUTO`.

```python
from pydantic import BaseModel, Field

from synthorg.core.types import NotBlankStr
from synthorg.ontology import ontology_entity


@ontology_entity
class CostCentre(BaseModel):
    """An accounting bucket that attributes spend to a budget owner."""

    name: NotBlankStr = Field(description="Human-readable cost-centre label")
    owner: NotBlankStr = Field(
        description="Identifier of the budget owner accountable for spend",
    )
```

Override the derived name, tier, or source by calling the decorator with
arguments:

```python
@ontology_entity(entity_name="Approval", tier=EntityTier.CORE)
class ApprovalItem(BaseModel):
    """A pending human decision gating an agent action."""
```

Framework models defined inside `synthorg.core` import the decorator from
`synthorg.ontology.decorator` rather than the package root, which keeps the
`core` import graph free of the heavier `persistence` and `security` edges.
Application code outside `core` can use the package-level
`from synthorg.ontology import ontology_entity`.

Only fields with a description are derived, because the description is what an
agent reads. A field without one is omitted from the definition.

## Option B: a domain entity via a company template

Domain entities that have no backing model are declared in a company template
(YAML), the ontology's ingestion format. They register at the `USER` tier with
the `CONFIG` source. Each entry maps to an
[`EntityEntry`][entry]; the `fields` mapping is field name to description.

```yaml
ontology:
  entities:
    entries:
      - name: CostCentre
        definition: >-
          An accounting bucket that attributes spend to a budget owner.
        fields:
          owner: Identifier of the budget owner accountable for spend.
        constraints:
          - Every cost centre maps to exactly one budget owner.
        disambiguation: Not a general-ledger account code.
```

`OntologyService.bootstrap_from_config()` reads the parsed `EntitiesConfig`,
builds an `EntityDefinition` per entry, and registers each one. Registration
is idempotent: an entity whose name already exists is skipped.

[entry]: ../design/ontology.md

## How a definition reaches an agent

The injection strategy turns registered definitions into agent context. With
the default `hybrid` strategy, `CORE` definitions are injected as a system
message and the rest stay available through an on-demand tool.

`PromptInjectionStrategy` lists the `CORE`-tier entities, renders each with
`format_entity()`, and packs them into a single system message up to the
configured `core_token_budget`:

```python
from synthorg.ontology.injection.prompt import format_entity

text = format_entity(definition)
# ## CostCentre
# An accounting bucket that attributes spend to a budget owner.
# Fields:
#   - owner: str -- Identifier of the budget owner accountable for spend
```

`ToolBasedInjectionStrategy` registers the `lookup_entity` tool instead, so an
agent fetches a definition by exact name or free-text query only when it needs
one. The `hybrid` strategy combines both: canonical `CORE` definitions up
front, everything else on demand. Select a strategy through configuration:

```yaml
ontology:
  injection:
    strategy: hybrid        # prompt | tool | hybrid | memory
    core_token_budget: 2000
    tool_name: lookup_entity
```

## Inspecting and managing at runtime

`OntologyService` is the orchestration entry point. It wraps the backend
repository and the versioning service:

```python
# Every @ontology_entity model registered in the backend.
registered = await ontology_service.bootstrap()

# Read paths.
cost_centre = await ontology_service.get("CostCentre")
core_only = await ontology_service.list_entities(tier=EntityTier.CORE)
matches = await ontology_service.search("budget")

# Version history (definitions are immutable; updates snapshot).
manifest = await ontology_service.get_version_manifest()
history = await ontology_service.list_versions("CostCentre")
```

The same operations are reachable over the REST API and the MCP tool surface
for operators and agents that act on the ontology directly.

## Observability

- `ontology.entity.decorator_registered` (debug): a model registered through
  the decorator.
- `ontology.bootstrap.completed` (info): bootstrap finished, with `total`,
  `registered`, and `skipped` counts.
- `ontology.config.loaded` (info): company-template entities registered.
- `ontology.entity.registered` / `ontology.entity.updated` /
  `ontology.entity.deleted`: backend writes.
- `ontology.injection.prepared` (debug): definitions packed into agent
  context, with `agent_id`, `entity_count`, and `strategy`.
- `ontology.tool.lookup` (debug): an agent invoked the `lookup_entity` tool.
- `ontology.sync.published` / `ontology.sync.skipped`: a definition published
  to organisational memory, or skipped because its content was unchanged.

Event-name constants live in `synthorg.observability.events.ontology`.

See [docs/design/ontology.md](../design/ontology.md) for the subsystem
architecture, the drift-detection and delegation-guard layers, and the
resolution rules.
