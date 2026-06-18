---
title: Dynamic Scoring
description: Register a custom candidate ranker, surface hyperparameters in settings, observe assignment decisions.
---

# Dynamic Scoring

Task assignment in SynthOrg runs a **filter -> score -> rank** pipeline. The
`ScoringBasedAssignmentStrategy` (`src/synthorg/engine/assignment/scoring_based.py`, a
single module, not a package) composes three collaborators: a `CandidatePoolFilter`
that narrows the eligible agents, a scorer that scores each candidate, and a
`CandidateRanker` that orders the scored candidates and picks the winner. The ranker is
the pluggable axis of variation: every scoring-based strategy filters and scores
identically and differs only in how it orders the result. This guide shows how to add a
custom ranker, expose its hyperparameters, and observe its outputs.

## Ranker contract

```python
from collections.abc import Sequence

from synthorg.engine.assignment.models import (
    AssignmentCandidate,
    AssignmentRequest,
)
from synthorg.engine.assignment.ranker_protocol import (
    CandidateRanker,
    RankingResult,
)


class TopScoreRanker:
    """Selects the highest-scoring candidate above a configurable floor.

    Candidates arrive already sorted by score descending. A real custom
    ranker would consult ``request`` for secondary keys (workload, cost,
    project context); this version drops anyone below ``score_floor`` and
    then trusts the score ordering.
    """

    def __init__(self, *, score_floor: float = 0.0) -> None:
        self._score_floor = score_floor

    @property
    def name(self) -> str:
        return "top_score"

    def rank(
        self,
        candidates: Sequence[AssignmentCandidate],
        request: AssignmentRequest,
    ) -> RankingResult:
        eligible = [c for c in candidates if c.score >= self._score_floor]
        ordered = eligible or list(candidates)
        selected, *alternatives = ordered
        return RankingResult(
            selected=selected,
            alternatives=tuple(alternatives),
            reason=f"top score {selected.score:.3f}",
        )
```

`rank` is synchronous and receives candidates already sorted by score descending. A
structural check (`isinstance(ranker, CandidateRanker)`) holds because the protocol is
`@runtime_checkable`.

## Registering the ranker

Rankers are registered as `(strategy_name, ranker_factory)` pairs in the single source
of truth, `src/synthorg/engine/assignment/registry.py`
(`_SCORING_STRATEGY_SPECS`), alongside the built-ins (`ScoreDescendingRanker`,
`WorkloadAscendingRanker`, `CostDescendingRanker`, `AuctionBidRanker`):

```python
# src/synthorg/engine/assignment/registry.py
_SCORING_STRATEGY_SPECS: tuple[tuple[str, Callable[[], CandidateRanker]], ...] = (
    (STRATEGY_NAME_ROLE_BASED, ScoreDescendingRanker),
    # ...
    ("top_score", TopScoreRanker),
)
```

## Hyperparameter surface

Tunable hyperparameters are exposed through the settings system so operators can adjust
without redeploying. Register a `SettingDefinition` under the relevant namespace, then
read the resolved value through a `ConfigResolver` (the typed accessors
`get_float` / `get_int` / `get_str` live on `ConfigResolver`, not on `SettingsService`,
whose `get()` returns the raw `SettingValue`):

```python
from synthorg.settings.resolver import ConfigResolver


async def build_top_score(resolver: ConfigResolver) -> TopScoreRanker:
    floor = await resolver.get_float("assignment", "score_floor")
    return TopScoreRanker(score_floor=floor)
```

## Worked example: unit-test a ranker

```python
import pytest


@pytest.mark.unit
def test_top_score_selected_and_not_in_alternatives(
    scored_candidates_factory, request_factory
) -> None:
    ranker = TopScoreRanker(score_floor=0.2)
    result = ranker.rank(
        scored_candidates_factory(scores=[0.9, 0.6, 0.1]),
        request_factory(),
    )
    assert result.selected.score == 0.9
    assert result.selected.agent_identity.id not in {
        a.agent_identity.id for a in result.alternatives
    }
```

## Where this fits

The ranker only orders candidates; it never mutates state. The
`ScoringBasedAssignmentStrategy` returns an `AssignmentResult` whose `reason` is the
ranker's explanation, which the engine logs for diagnostics. For the assignment
subsystem overview see [docs/design/engine.md](../design/engine.md).
