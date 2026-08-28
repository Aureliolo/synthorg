---
title: Custom Rules & Meta-Loop
description: Plug a signal rule into the meta-loop, observe its evaluation, gate auto-evolution on rule outcomes.
---

# Custom Rules & Meta-Loop

The meta-loop (`src/synthorg/meta/`) is SynthOrg's reflective layer: it aggregates
company-wide signals into an `OrgSignalSnapshot`, evaluates rules over that snapshot,
and proposes adaptations through the evolution pipeline. Rules let operators encode
local invariants (e.g. "react when the org success rate drops below a threshold")
without forking the core. Rules only PROPOSE; every adaptation still passes through
the proposal guards and human approval.

!!! warning "Off by default, and nothing here runs unattended"

    `self_improvement.enabled` ships `false`, so no snapshot is aggregated and no
    rule is evaluated until an operator turns the loop on. Every strategy toggle
    beneath it is off too, apart from config tuning, which has no effect while the
    master switch is off. The approval gate is not a phase the loop graduates from:
    a proposal reaches a human whether or not a rule fired, and no live end-to-end
    run of this loop has happened.

## Concepts

- **`SignalRule`**: a class implementing the `SignalRule` protocol. It inspects an
  `OrgSignalSnapshot` and returns a `RuleMatch` when its pattern is detected, or
  `None` otherwise.
- **`RuleMatch`**: carries `rule_name`, a `RuleSeverity` (`info` / `warning` /
  `critical`), a human-readable `description`, a `signal_context` dict, and the
  `suggested_altitudes` (which improvement strategies should generate proposals).
- **Meta-loop step**: one signal-aggregation -> rule fan-out -> match collection ->
  optional evolution proposal.

## SignalRule contract

```python
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    OrgSignalSnapshot,
    ProposalAltitude,
    RuleMatch,
    RuleSeverity,
)
from synthorg.meta.protocol import SignalRule


class HighRetryRule:
    """Fires when the org-wide error count exceeds a threshold."""

    def __init__(self, *, threshold: int = 10) -> None:
        self._threshold = threshold

    @property
    def name(self) -> NotBlankStr:
        return NotBlankStr("high_retry")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        return (ProposalAltitude.CONFIG_TUNING,)

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        errors = snapshot.errors
        if errors.total_findings <= self._threshold:
            return None
        return RuleMatch(
            rule_name=self.name,
            severity=RuleSeverity.WARNING,
            description=f"{errors.total_findings} errors exceed threshold {self._threshold}",
            signal_context={"total_findings": errors.total_findings},
            suggested_altitudes=self.target_altitudes,
        )
```

`evaluate` is synchronous and pure: it reads the snapshot and returns a match or
`None`. A structural check (`isinstance(rule, SignalRule)`) holds because the protocol
is `@runtime_checkable`.

## Built-in rules

`default_rules()` in `src/synthorg/meta/rules/builtin.py` wires the shipped set:
`QualityDecliningRule`, `SuccessRateDropRule`, `BudgetOverrunRule`,
`CoordinationCostRatioRule`, `CoordinationOverheadRule`, `StragglerBottleneckRule`,
`RedundancyRule`, `ErrorSpikeRule`, and `BenchmarkRegressionRule` (the last defined
in the sibling `benchmark_rule.py`). Add a new code-level rule by implementing the
protocol and including it in `default_rules()`.

## Custom declarative rules (dashboard)

Operators author rules at runtime without code through the dashboard rather than via
YAML. A `CustomRuleDefinition` is stored through the `CustomRuleController` at
`/api/v1/meta/custom-rules` (list, get, create, update, delete, toggle, plus
`/metrics` for the available metric paths and `/preview` for a dry run) and compiled
into a `DeclarativeRule` (`src/synthorg/meta/rules/custom.py`) that implements the
same `SignalRule` protocol. Each definition carries a `name`, a `metric_path` (a
dot-notation path into `OrgSignalSnapshot`, validated against `METRIC_REGISTRY`), a
`Comparator`, a numeric `threshold`, a `RuleSeverity`, and the `target_altitudes`:

```python
from synthorg.meta.rules.custom import DeclarativeRule

# definition: a CustomRuleDefinition loaded from the custom-rule store
rule: DeclarativeRule = DeclarativeRule(definition)
match = rule.evaluate(snapshot)  # RuleMatch | None, same protocol as built-in rules
```

Severity orders the fan-out and nothing else: `RuleEngine` returns matched rules
sorted critical-first, so the most serious signal leads the context handed to the
improvement strategies. It is not a veto. A `critical` match does not block a
proposal, because every proposal is already blocked on the approval gate.

## Worked example: unit-test a rule

```python
import pytest

from synthorg.meta.models import RuleSeverity


@pytest.mark.unit
def test_high_retry_fires_above_threshold(org_signal_snapshot_factory) -> None:
    snapshot = org_signal_snapshot_factory(total_findings=25)
    match = HighRetryRule(threshold=10).evaluate(snapshot)
    assert match is not None
    assert match.severity is RuleSeverity.WARNING
    assert match.signal_context["total_findings"] == 25


@pytest.mark.unit
def test_high_retry_silent_below_threshold(org_signal_snapshot_factory) -> None:
    snapshot = org_signal_snapshot_factory(total_findings=3)
    assert HighRetryRule(threshold=10).evaluate(snapshot) is None
```

## Where this fits

A firing rule does NOT itself mutate the system: it returns a `RuleMatch` that the
meta-loop aggregates and feeds to the improvement strategies, proposal guards, and
human approval. For the broader meta-loop and self-improvement architecture, see
[docs/design/self-improvement.md](../design/self-improvement.md).
