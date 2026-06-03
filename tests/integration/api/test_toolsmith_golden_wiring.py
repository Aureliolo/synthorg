"""Integration coverage for the eval-backed golden-scorecard wiring.

Exercises the real golden-company eval harness end to end (it boots an
``AgentEngine`` and runs the reference briefs, including subprocess
grading), so it is integration-tier rather than unit: it resolves the
in-repo ``evals/`` tree and would raise ``GoldenScorecardUnavailableError``
in a packaged install. The deterministic ``score()`` contract (call
ordering, ``candidate == baseline`` semantics, reject/accept) is covered
at unit tier in ``tests/unit/meta/toolsmith/test_golden_scorecard.py``.
"""

from datetime import UTC, datetime

import pytest

from synthorg.api.lifecycle_helpers.toolsmith_wiring import (
    _build_golden_scorecard_provider,
)
from synthorg.meta.toolsmith.golden_scorecard import EvalGoldenScorecardProvider
from synthorg.meta.toolsmith.models import ToolBlueprint

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id="bp-1",
        name="synthorg_textkit_slugify",
        description="Slugify text.",
        capability="textkit:slugify",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        script_body="print('{}')",
        action_type="code:read",
        created_at=_NOW,
    )


async def test_eval_provider_runs_the_real_golden_suite() -> None:
    # Proves the wired runner actually drives run_benchmark_async over the
    # reference golden company: paths resolve, kwargs are correct, and
    # Scorecard.total is extracted. The deterministic eval ignores the
    # candidate tool, so baseline == candidate (a no-regression smoke
    # check) and both totals are a positive suite score.
    provider = _build_golden_scorecard_provider("eval")
    assert isinstance(provider, EvalGoldenScorecardProvider)

    baseline, candidate = await provider.score(_blueprint())

    assert baseline == candidate
    assert baseline > 0
