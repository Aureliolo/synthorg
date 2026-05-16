---
title: Custom Rules & Meta-Loop
description: Plug a rule into the meta-loop, observe its evaluation, gate auto-evolution on rule outcomes.
---

# Custom Rules & Meta-Loop

The meta-loop (`src/synthorg/meta/loop/`) is SynthOrg's reflective layer: it observes agent behaviour, evaluates rules over the observed evidence, and proposes adaptations through the evolution pipeline. Custom rules let operators encode local invariants (e.g. "no agent retries the same failing tool more than five times in a sprint") without forking the core.

## Concepts

- **Rule**: a callable that accepts a `MetaLoopContext` and returns a `RuleVerdict` (pass / warn / fail / skip).
- **Verdict**: carries severity, a structured `details` dict, and an optional `proposal` suggesting an adaptation.
- **Meta-loop step**: one observation -> rule fan-out -> verdict aggregation -> optional evolution proposal.

## Rule contract

```python
from synthorg.meta.rules.protocol import Rule, RuleVerdict
from synthorg.meta.loop.context import MetaLoopContext


class HighFailRateRule:
    name = "high_fail_rate"

    async def evaluate(self, context: MetaLoopContext) -> RuleVerdict:
        snapshot = await context.recent_task_outcomes(minutes=60)
        fails = sum(1 for s in snapshot if s.outcome == "failed")
        total = len(snapshot)
        if total == 0:
            return RuleVerdict.skip(reason="no_observations")
        ratio = fails / total
        if ratio > 0.3:
            return RuleVerdict.fail(
                rule=self.name,
                details={"ratio": ratio, "total": total},
            )
        return RuleVerdict.pass_(rule=self.name, details={"ratio": ratio})
```

## Registering the rule

Rules live in `src/synthorg/meta/rules/` and are registered with the rule registry:

```python
# src/synthorg/meta/rules/__init__.py
from synthorg.core.registry.strategy import StrategyRegistry
from synthorg.meta.rules.high_fail_rate import HighFailRateRule
from synthorg.meta.rules.protocol import Rule

RULE_REGISTRY: StrategyRegistry[Rule] = StrategyRegistry(
    {
        HighFailRateRule.name: HighFailRateRule,
    },
    kind="meta_loop_rule",
)
```

## Configuration

Rules opt in per-deployment under the `meta_loop.rules` namespace:

```yaml
meta_loop:
  enabled: true
  rules:
    - name: high_fail_rate
      severity: warn
    - name: budget_drift
      severity: fail
      params:
        threshold: 0.15
```

A failing verdict with `severity: fail` blocks the meta-loop step from emitting an evolution proposal; the violation surfaces on the operator dashboard at `/dashboard/meta-loop`.

## Worked example: observe a meta-loop step

Add a unit test under `tests/unit/meta/rules/test_high_fail_rate.py`:

```python
import pytest

from synthorg.meta.rules.high_fail_rate import HighFailRateRule


@pytest.mark.unit
async def test_fail_ratio_above_threshold_fails(meta_loop_context_with_outcomes) -> None:
    context = meta_loop_context_with_outcomes(
        outcomes=["succeeded"] * 4 + ["failed"] * 6,
    )
    verdict = await HighFailRateRule().evaluate(context)
    assert verdict.severity == "fail"
    assert verdict.details["ratio"] == pytest.approx(0.6)


@pytest.mark.unit
async def test_no_observations_skipped(meta_loop_context_with_outcomes) -> None:
    context = meta_loop_context_with_outcomes(outcomes=[])
    verdict = await HighFailRateRule().evaluate(context)
    assert verdict.severity == "skip"
```

The `meta_loop_context_with_outcomes` fixture lives in `tests/unit/meta/conftest.py`.

## Observability

Every rule evaluation emits:

- `meta.rule.evaluated`: with `name`, `severity`, `details`.
- `meta.rule.proposal_emitted`: when the verdict carries an adaptation proposal.
- `meta.rule.skipped`: when prerequisites are absent (no observations, missing context).

The `synthorg_meta_rule_evaluations_total` counter has bounded labels `rule` (registry-bound) and `severity`.

## Where this fits

A failing rule does NOT itself mutate the system: it returns a verdict that the meta-loop coordinator aggregates. Adaptation lands through the evolution pipeline (see [docs/design/evolution.md](../design/evolution.md)). For the broader meta-loop architecture, see [docs/design/meta-loop.md](../design/meta-loop.md).
