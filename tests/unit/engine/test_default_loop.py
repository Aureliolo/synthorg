"""Every in-flight control the engine holds reaches the loop it builds.

The engine ships one execution loop, so ``_make_default_loop`` is the single
place a control can be dropped: a builder that accepted its collaborators as
``**kwargs`` and named none of them silently ran ungoverned, which is what
these tests exist to refuse. Each control is a distinct sentinel so a swap
between two of them fails as loudly as a drop.

``with_checkpoint_callback`` is the second such place: resume rebuilds the
loop to attach a callback the engine could not supply at construction, and a
rebuild naming its fields at the call site drops whichever control is added
next.
"""

from typing import cast, override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.loop_protocol import TurnObserver, TurnProgress
from synthorg.engine.quality.classifier import StepQualityClassifier
from synthorg.engine.react_loop import ReactLoop
from synthorg.engine.stagnation.protocol import StagnationDetector
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.registry import ToolRegistry
from tests._shared import FakeClock, mock_of
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit


class _StubTool(BaseTool):
    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="stub")


async def _compaction(ctx: AgentContext) -> AgentContext | None:
    del ctx
    return None


async def _observe(progress: TurnProgress) -> None:
    del progress


def _controls() -> dict[str, object]:
    """Build one distinct sentinel per in-flight control.

    Returns:
        The engine constructor keywords for the controls a loop must carry.
    """
    return {
        "stagnation_detector": mock_of[StagnationDetector](),
        "step_classifier": mock_of[StepQualityClassifier](),
        "steering_inbox": mock_of[SteeringInbox](),
        "compaction_callback": _compaction,
    }


def _engine(controls: dict[str, object]) -> AgentEngine:
    return AgentEngine(
        provider=ScriptedProvider([]),
        tool_registry=ToolRegistry(
            [_StubTool(name="stub", category=ToolCategory.OTHER)]
        ),
        approval_store=ApprovalStore(),
        stagnation_detector=cast("StagnationDetector", controls["stagnation_detector"]),
        step_classifier=cast("StepQualityClassifier", controls["step_classifier"]),
        steering_inbox=cast("SteeringInbox", controls["steering_inbox"]),
        compaction_callback=cast("CompactionCallback", controls["compaction_callback"]),
    )


class TestEngineDefaultLoop:
    def test_builds_a_react_loop(self) -> None:
        engine = _engine(_controls())
        assert isinstance(engine._loop, ReactLoop)

    def test_every_control_reaches_the_loop(self) -> None:
        controls = _controls()
        loop = _engine(controls)._loop
        assert isinstance(loop, ReactLoop)
        assert loop.stagnation_detector is controls["stagnation_detector"]
        assert loop.steering_inbox is controls["steering_inbox"]
        assert loop.compaction_callback is controls["compaction_callback"]
        assert loop._step_classifier is controls["step_classifier"]

    def test_approval_gate_reaches_the_loop(self) -> None:
        # Built by the engine from its approval store rather than injected,
        # so it is the one control whose identity the caller cannot supply.
        loop = _engine(_controls())._loop
        assert isinstance(loop, ReactLoop)
        assert loop.approval_gate is not None


class TestCheckpointRebuild:
    def test_rebuild_preserves_every_control(self) -> None:
        controls = _controls()
        observer = _observe
        clock = FakeClock()
        original = ReactLoop(
            approval_gate=None,
            stagnation_detector=cast(
                "StagnationDetector", controls["stagnation_detector"]
            ),
            compaction_callback=cast(
                "CompactionCallback", controls["compaction_callback"]
            ),
            steering_inbox=cast("SteeringInbox", controls["steering_inbox"]),
            step_classifier=cast("StepQualityClassifier", controls["step_classifier"]),
            turn_observer=cast("TurnObserver", observer),
            clock=clock,
        )

        async def _callback(ctx: AgentContext) -> None:
            del ctx

        rebuilt = original.with_checkpoint_callback(
            cast("CheckpointCallback", _callback)
        )

        assert rebuilt is not original
        assert rebuilt._checkpoint_callback is _callback
        assert rebuilt.stagnation_detector is controls["stagnation_detector"]
        assert rebuilt.compaction_callback is controls["compaction_callback"]
        assert rebuilt.steering_inbox is controls["steering_inbox"]
        assert rebuilt._step_classifier is controls["step_classifier"]
        assert rebuilt._turn_observer is observer
        assert rebuilt._clock is clock
