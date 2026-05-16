---
title: Ontology Extension
description: Register a new domain term, attach a description, observe how it threads into agent prompts.
---

# Ontology Extension

SynthOrg's ontology lives at `synthorg.ontology`. Each domain term carries a name, a description, and optional examples; the injection profile pipes terms into agent prompts so the LLM has consistent vocabulary. This guide walks through registering a new term and verifying it surfaces in a prompt.

## Concepts

- **Domain term**: a noun-phrase concept the platform knows about (e.g. `task_status`, `sprint`, `cost_record`).
- **Injection profile**: a named bundle of terms applied to a prompt class via `synthorg.ontology.injection.profile`.
- **Resolution order**: explicit definition > company-template override > built-in default.

## Registering a new term

Built-in terms live in `src/synthorg/ontology/terms/`. Add a new file or extend an existing module:

```python
# src/synthorg/ontology/terms/finance.py
from synthorg.ontology.term import DomainTerm

cost_centre = DomainTerm(
    name="cost_centre",
    description=(
        "An accounting bucket used to attribute spend to a "
        "department or project."
    ),
    examples=(
        "engineering",
        "rd-platform",
        "client-services",
    ),
)
```

Register the term with the ontology registry:

```python
# src/synthorg/ontology/registry.py
from synthorg.ontology.terms.finance import cost_centre

ONTOLOGY_REGISTRY.register(cost_centre)
```

## Worked example: thread the term into a prompt

Add the term to the relevant injection profile:

```python
# src/synthorg/ontology/injection/profile.py
FINANCE_PROFILE = InjectionProfile(
    name="finance",
    terms=("cost_record", "cost_centre", "budget"),
)
```

Reference the profile in a prompt class:

```python
class CostExplainer:
    PROFILE = FINANCE_PROFILE

    def render(self, cost: CostRecord) -> str:
        ontology_block = self.PROFILE.render_for_prompt()
        return f"{ontology_block}\n\nExplain: {cost.model_dump_json()}"
```

Verify the term appears in the rendered prompt:

```python
from synthorg.ontology.registry import ONTOLOGY_REGISTRY
from synthorg.ontology.injection.profile import FINANCE_PROFILE

rendered = FINANCE_PROFILE.render_for_prompt()
assert "cost_centre" in rendered
assert "An accounting bucket" in rendered
```

## Company-template overrides

A company template (YAML) can shadow built-in terms:

```yaml
ontology:
  terms:
    - name: cost_centre
      description: |
        Cost centres in this company map to budget owners rather
        than accounting buckets; treat them as the canonical
        spend-attribution unit.
      examples:
        - acme.platform
        - acme.research
```

The company-template override flows through `synthorg.templates.loader.apply_ontology_overrides` at startup and is the canonical source once applied. Operators changing the override do not need to redeploy; reload the template via `synthorg config reload-template`.

## Observability

Term resolution emits `ontology.term.resolved` with `name` and `source` (`built_in` / `template`) at debug. Missing terms (referenced by a profile but not registered) emit `ontology.term.missing` at warning.

## Adding a profile

1. Define the `InjectionProfile` in `src/synthorg/ontology/injection/profile.py`.
2. Reference the profile from the relevant prompt class via `PROFILE = ...`.
3. Add `tests/unit/ontology/test_<profile>.py` asserting the rendered block contains every expected term.

See [docs/design/ontology.md](../design/ontology.md) for the broader design and resolution rules.
