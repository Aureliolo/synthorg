# module-kind: tests
"""Golden-benchmark A/B promotion proof for the finetune gate.

The continual-improvement finetune "A/Bs on the golden benchmark; promotes ONLY
on a measured win". This exercises that end to end under the deterministic sim
harness: ``evals.run.run_benchmark_async`` scores a *candidate* embedder (one
that recalls the org's lesson, modelling a fine-tuned model that retrieves the
right passage) and a *base* embedder (no recall), and the two real
``Scorecard.total`` values are fed to the SAME pure ``should_promote`` gate the
fine-tune orchestrator uses. A win promotes; a tie or a regression does not.

Only the LLM is a deterministic stand-in (keying solely on the generic
``<memory-entry>`` marker). The capture -> store -> retrieve -> inject pipeline
and the scorer are the real ones, so the score difference is earned, not faked.
src never imports ``evals``: the gate is pure and this test (in the evals layer)
supplies the benchmark scores -- the same seam the learning curve uses.
"""

import json
from pathlib import Path
from typing import Final

import pytest

from evals.loader.anchors import load_anchor_set
from evals.run import run_benchmark_async
from evals.scoring.judged import ScriptedJudge
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.prompt_safety import TAG_MEMORY_ENTRY
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.embedding.promotion import should_promote
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)

pytestmark = pytest.mark.unit

_EVALS: Final[Path] = Path(__file__).resolve().parents[3] / "evals"
_REFERENCE: Final[Path] = _EVALS / "baselines" / "reference.yaml"
_BRIEFS: Final[Path] = _EVALS / "briefs"
_ANCHORS: Final[Path] = _EVALS / "anchors"
_RUBRIC_ID: Final[str] = "default-bench"

_MEMORY_MARKER: Final[str] = f"<{TAG_MEMORY_ENTRY}>"
_PROPOSER_MARKER: Final[str] = "failure analysis assistant"
_BRIEF_TITLE: Final[str] = "checkout resilience"
_GOOD_DELIVERABLE: Final[str] = (
    "A fully resilient checkout flow that recovers from every failure."
)
_NAIVE_DELIVERABLE: Final[str] = "I could not complete the checkout flow."
_WARMUP_ROUNDS: Final[int] = 2

_PROPOSAL_JSON: Final[str] = json.dumps(
    {
        "discovery": f"For {_BRIEF_TITLE}, recover from the failure next time.",
        "condition": f"A {_BRIEF_TITLE} task fails on the first attempt.",
        "action": "Apply the recorded corrected approach.",
        "rationale": "The prior failure already mapped the corrected path.",
        "execution_steps": ["Recall the lesson", "Take the corrected branch"],
        "confidence": 0.9,
        "tags": ["checkout"],
    },
)


class _CurveStrategy:
    """Deterministic LLM: fail first, propose a lesson, succeed once recalled."""

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return the deterministic completion for this call."""
        del tools, config
        usage = TokenUsage(input_tokens=8, output_tokens=4, cost=0.001)
        if any(
            m.role == MessageRole.SYSTEM
            and _PROPOSER_MARKER in (m.content or "").lower()
            for m in messages
        ):
            return CompletionResponse(
                content=_PROPOSAL_JSON,
                finish_reason=FinishReason.STOP,
                usage=usage,
                model=model,
            )
        if any(_MEMORY_MARKER in (m.content or "") for m in messages):
            return CompletionResponse(
                content=_GOOD_DELIVERABLE,
                finish_reason=FinishReason.STOP,
                usage=usage,
                model=model,
            )
        return CompletionResponse(
            content=_NAIVE_DELIVERABLE,
            finish_reason=FinishReason.ERROR,
            usage=usage,
            model=model,
        )


def _curve_judge() -> ScriptedJudge:
    """Calibrated judge: echoes anchors, scores the good deliverable highest."""
    anchors = load_anchor_set(_ANCHORS, _RUBRIC_ID)
    responses: dict[str, dict[str, float]] = {
        item.output: dict(item.hand_scores) for item in anchors.items
    }
    responses[_GOOD_DELIVERABLE] = {"correctness": 1.0}
    return ScriptedJudge(responses=responses, default_scores={"correctness": 0.0})


async def _run_round(
    out_dir: Path,
    *,
    memory_backend: InMemoryBackend | None,
    procedural_config: ProceduralMemoryConfig,
) -> int:
    """Run one benchmark round and return the suite total.

    Returns:
        The ``Scorecard.total`` for the round.
    """
    scorecard = await run_benchmark_async(
        company_config=_REFERENCE,
        brief_suite=_BRIEFS,
        out_dir=out_dir,
        anchors_dir=_ANCHORS,
        provider=ScriptedDriver("benchmark-provider", strategy=_CurveStrategy()),
        judge=_curve_judge(),
        memory_backend=memory_backend,
        procedural_config=procedural_config,
    )
    return scorecard.total


async def _score_candidate_embedder(tmp_path: Path) -> int:
    """Benchmark a fine-tuned embedder that recalls the org's lesson.

    Returns:
        The candidate's ``Scorecard.total`` after the backend has been warmed.
    """
    backend = InMemoryBackend()
    await backend.connect()
    config = ProceduralMemoryConfig(model="test-small-001")
    for index in range(_WARMUP_ROUNDS):
        await _run_round(
            tmp_path / f"warmup-{index}",
            memory_backend=backend,
            procedural_config=config,
        )
    return await _run_round(
        tmp_path / "candidate",
        memory_backend=backend,
        procedural_config=config,
    )


async def _score_base_embedder(tmp_path: Path) -> int:
    """Benchmark the incumbent embedder that does not recall the lesson.

    Returns:
        The base model's ``Scorecard.total``.
    """
    return await _run_round(
        tmp_path / "base",
        memory_backend=None,
        procedural_config=ProceduralMemoryConfig(
            model="test-small-001",
            enabled=False,
        ),
    )


async def test_promotion_gate_decides_on_golden_benchmark_ab(
    tmp_path: Path,
) -> None:
    """A measured benchmark win promotes; a tie or regression does not."""
    candidate_total = await _score_candidate_embedder(tmp_path)
    base_total = await _score_base_embedder(tmp_path)

    gain = candidate_total - base_total
    assert gain > 0, (
        "the fine-tuned embedder must measurably beat base on the golden "
        f"benchmark (candidate={candidate_total}, base={base_total})"
    )
    # A margin strictly inside the real gain: the win clears it, a tie cannot.
    margin = gain / 2

    # Win: the candidate beats base by more than the margin -> promote.
    assert should_promote(base_total, candidate_total, margin=margin) is True
    # Tie: identical totals never promote, regardless of margin.
    assert should_promote(candidate_total, candidate_total, margin=margin) is False
    # Regression: the candidate scores below base -> never promote.
    assert should_promote(candidate_total, base_total, margin=margin) is False
