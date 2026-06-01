# module-kind: tests
"""Learning-curve acceptance under the deterministic benchmark runner.

Re-runs ``evals.run.run_benchmark_async`` across rounds while a single procedural
memory backend accumulates. The benchmark score RISES because the company
produces a better deliverable once it has recalled the lesson from its first
failure, and stays FLAT when procedural memory is disabled. Only the LLM is a
deterministic stand-in, keying solely on the generic ``<memory-entry>`` marker
(never the brief). The capture -> store -> retrieve -> inject pipeline and the
scorer are real.
"""

import json
from pathlib import Path
from typing import Final

import pytest

from evals.loader.anchors import load_anchor_set
from evals.run import run_benchmark_async
from evals.scoring.judged import ScriptedJudge
from synthorg.engine.prompt_safety import TAG_MEMORY_ENTRY
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import FinishReason, MessageRole
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
# Matches the brief title (the runner's retrieval query) so the stored lesson
# is surfaced on the next run by the InMemory substring match.
_BRIEF_TITLE: Final[str] = "checkout resilience"
_GOOD_DELIVERABLE: Final[str] = (
    "A fully resilient checkout flow that recovers from every failure."
)
_NAIVE_DELIVERABLE: Final[str] = "I could not complete the checkout flow."

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
    """Run one benchmark round and return the suite total."""
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


async def test_curve_rises_with_learning(tmp_path: Path) -> None:
    """With procedural memory active, the benchmark score rises across rounds."""
    backend = InMemoryBackend()
    await backend.connect()
    config = ProceduralMemoryConfig(model="test-small-001")

    totals = [
        await _run_round(
            tmp_path / f"round-{index}",
            memory_backend=backend,
            procedural_config=config,
        )
        for index in range(3)
    ]

    assert totals[0] < totals[-1], f"curve did not rise: {totals}"
    # First round fails (no lesson yet); a later round recalls it and succeeds.
    assert totals[0] == 0
    assert totals[-1] > 0


async def test_curve_is_flat_when_learning_disabled(tmp_path: Path) -> None:
    """With procedural memory disabled, the score stays flat (no lesson recall)."""
    disabled = ProceduralMemoryConfig(model="test-small-001", enabled=False)

    totals = [
        await _run_round(
            tmp_path / f"round-{index}",
            memory_backend=None,
            procedural_config=disabled,
        )
        for index in range(3)
    ]

    assert totals[0] == totals[-1], f"curve should be flat: {totals}"
    assert all(total == 0 for total in totals)
