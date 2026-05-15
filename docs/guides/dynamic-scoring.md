---
title: Dynamic Scoring
description: Register a custom scoring strategy, surface hyperparameters in settings, observe score drift.
---

# Dynamic Scoring

Scoring drives every "what to do next" decision in SynthOrg: which task to assign, which agent to pick, which strategy to apply. The scoring layer at `synthorg.engine.assignment.scoring` is pluggable: each strategy is a `ScoringStrategy` implementation registered in the strategy registry. This guide shows how to add a custom strategy, expose its hyperparameters, and observe its outputs.

## Strategy contract

```python
from synthorg.engine.assignment.scoring.protocol import (
    ScoringStrategy,
    ScoringContext,
    ScoreResult,
)


class FreshnessBoostedScorer:
    name = "freshness_boosted"

    def __init__(self, *, boost_factor: float = 0.2) -> None:
        self._boost = boost_factor

    async def score(self, context: ScoringContext) -> ScoreResult:
        base = await context.base_score()
        recency_bonus = self._recency_term(context)
        return ScoreResult(
            value=base + self._boost * recency_bonus,
            details={"base": base, "recency_bonus": recency_bonus},
        )

    def _recency_term(self, context: ScoringContext) -> float:
        elapsed = context.now - context.candidate.last_active
        return max(0.0, 1.0 - (elapsed.total_seconds() / 86400))
```

## Registering the strategy

Add to the registry:

```python
# src/synthorg/engine/assignment/scoring/__init__.py
from synthorg.core.registry.strategy import StrategyRegistry
from synthorg.engine.assignment.scoring.freshness_boosted import (
    FreshnessBoostedScorer,
)
from synthorg.engine.assignment.scoring.protocol import ScoringStrategy

SCORING_STRATEGY_REGISTRY: StrategyRegistry[ScoringStrategy] = StrategyRegistry(
    {
        FreshnessBoostedScorer.name: FreshnessBoostedScorer,
    },
    kind="scoring_strategy",
)
```

## Hyperparameter surface

Strategies that carry tunable hyperparameters expose them through the settings system so operators can adjust without redeploying:

```python
# src/synthorg/settings/definitions/scoring.py
from synthorg.settings import SettingDefinition

SCORING_DEFINITIONS = (
    SettingDefinition(
        namespace="scoring",
        key="freshness_boost_factor",
        default=0.2,
        validator=lambda v: 0.0 <= v <= 1.0,
        description="Multiplier on the freshness bonus term.",
    ),
)
```

The factory reads the resolved value at strategy construction time:

```python
async def build_freshness_boosted(
    settings: SettingsService,
) -> FreshnessBoostedScorer:
    factor = await settings.get_float(
        "scoring", "freshness_boost_factor"
    )
    return FreshnessBoostedScorer(boost_factor=factor)
```

## Configuration

Pick the active strategy via the `scoring.strategy` setting:

```yaml
scoring:
  strategy: freshness_boosted
  freshness_boost_factor: 0.3
```

The setting is hot-reloadable: a change via `synthorg config set scoring.strategy <name>` swaps the active strategy on the next assignment decision.

## Worked example: end-to-end test

```python
# tests/unit/engine/scoring/test_freshness_boosted.py
import pytest

from synthorg.engine.assignment.scoring.freshness_boosted import (
    FreshnessBoostedScorer,
)


@pytest.mark.unit
async def test_recent_candidate_outscores_stale(
    scoring_context_factory,
) -> None:
    scorer = FreshnessBoostedScorer(boost_factor=0.5)
    recent = await scorer.score(scoring_context_factory(hours_idle=0))
    stale = await scorer.score(scoring_context_factory(hours_idle=72))
    assert recent.value > stale.value
    assert recent.details["recency_bonus"] > stale.details["recency_bonus"]
```

The `scoring_context_factory` fixture lives in `tests/unit/engine/scoring/conftest.py`.

## Observability

Every score emission fires `scoring.score.computed` with `strategy`, `score`, and the `details` payload. The dashboard `Scoring` panel charts the rolling p50/p95/p99 score per strategy so operators can detect drift.

For the broader scoring architecture and the existing strategies (composite, weighted, ranked, multi-objective), see [docs/design/scoring.md](../design/scoring.md) and [docs/reference/scoring-hyperparameters.md](../reference/scoring-hyperparameters.md).
