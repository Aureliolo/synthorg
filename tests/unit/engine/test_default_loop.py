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

import inspect
from typing import cast, override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.background_job_watch import BackgroundJobWatcher
from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.loop_controls import LoopControls
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
        "background_job_watcher": mock_of[BackgroundJobWatcher](),
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
        background_job_watcher=cast(
            "BackgroundJobWatcher", controls["background_job_watcher"]
        ),
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
        assert loop.step_classifier is controls["step_classifier"]
        assert loop.background_job_watcher is controls["background_job_watcher"]

    def test_approval_gate_reaches_the_loop(self) -> None:
        # Built by the engine from its approval store rather than injected,
        # so the caller cannot supply a sentinel. Asserting identity against
        # the engine's own gate still catches a builder that constructs a
        # SECOND gate instead of forwarding the one the engine holds.
        engine = _engine(_controls())
        loop = engine._loop
        assert isinstance(loop, ReactLoop)
        assert loop.approval_gate is engine._approval_gate
        assert loop.approval_gate is not None

    def test_no_gate_when_no_approval_store(self) -> None:
        """Without a store there is nothing to park against, so no gate."""
        controls = _controls()
        engine = AgentEngine(
            provider=ScriptedProvider([]),
            tool_registry=ToolRegistry(
                [_StubTool(name="stub", category=ToolCategory.OTHER)]
            ),
            approval_store=None,
            stagnation_detector=cast(
                "StagnationDetector", controls["stagnation_detector"]
            ),
        )
        loop = engine._loop
        assert isinstance(loop, ReactLoop)
        assert loop.approval_gate is None


class TestCheckpointRebuild:
    def test_rebuild_preserves_every_control(self) -> None:
        controls = _controls()
        observer = _observe
        clock = FakeClock()
        gate = mock_of[ApprovalGate]()
        original = ReactLoop(
            approval_gate=cast("ApprovalGate", gate),
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
            background_job_watcher=cast(
                "BackgroundJobWatcher", controls["background_job_watcher"]
            ),
        )

        async def _callback(ctx: AgentContext) -> None:
            del ctx

        rebuilt = original.with_checkpoint_callback(
            cast("CheckpointCallback", _callback)
        )

        assert rebuilt is not original
        assert rebuilt._checkpoint_callback is _callback
        # The approval gate is the one whose loss is security-relevant: a
        # resumed run that stopped parking on escalations would execute what
        # it should have held. It has to be a real sentinel here, because a
        # ``None`` original reads identically whether it survived or not.
        assert rebuilt.approval_gate is gate
        assert rebuilt.stagnation_detector is controls["stagnation_detector"]
        assert rebuilt.compaction_callback is controls["compaction_callback"]
        assert rebuilt.steering_inbox is controls["steering_inbox"]
        assert rebuilt.step_classifier is controls["step_classifier"]
        assert rebuilt._turn_observer is observer
        assert rebuilt._clock is clock
        assert rebuilt.background_job_watcher is controls["background_job_watcher"]

    def test_every_constructor_field_survives_a_rebuild(self) -> None:
        """Derived from the signature, so a field added later is covered.

        The defect this method replaced dropped three fields by naming its
        copy at the call site. A hand-written assertion list has the same
        failure mode one field later, so the field set comes from
        ``__init__`` rather than from this test.
        """
        original = ReactLoop(
            approval_gate=cast("ApprovalGate", mock_of[ApprovalGate]()),
            stagnation_detector=cast(
                "StagnationDetector", mock_of[StagnationDetector]()
            ),
            compaction_callback=_compaction,
            steering_inbox=cast("SteeringInbox", mock_of[SteeringInbox]()),
            step_classifier=cast(
                "StepQualityClassifier", mock_of[StepQualityClassifier]()
            ),
            turn_observer=_observe,
            clock=FakeClock(),
            background_job_watcher=cast(
                "BackgroundJobWatcher", mock_of[BackgroundJobWatcher]()
            ),
        )

        async def _callback(ctx: AgentContext) -> None:
            del ctx

        rebuilt = original.with_checkpoint_callback(
            cast("CheckpointCallback", _callback)
        )

        carried = [
            name
            for name in inspect.signature(ReactLoop.__init__).parameters
            if name not in {"self", "checkpoint_callback"}
        ]
        assert carried, "signature introspection found no controls to check"
        for name in carried:
            attribute = f"_{name}"
            assert getattr(rebuilt, attribute) is getattr(original, attribute), name

    def test_a_specialised_loop_rebuilds_as_itself(self) -> None:
        """A loop the engine did not build survives the resume rebuild.

        Two ways it would not. Naming ``ReactLoop`` in the rebuild returns the
        base, so a resumed run silently loses whatever the subclass added; and
        reaching ``type(self)`` with only the base controls meets a
        ``TypeError`` for a subclass whose constructor takes more. Overriding
        the rebuild is what lets such a loop answer for its own construction,
        and ``rebuild_controls`` is what lets it do so without restating the
        base half.
        """

        class _SpecialisedLoop(ReactLoop):
            def __init__(
                self,
                checkpoint_callback: CheckpointCallback | None = None,
                *,
                marker: str,
                **controls: object,
            ) -> None:
                super().__init__(
                    checkpoint_callback,
                    **cast("LoopControls", controls),
                )
                self.marker = marker

            @override
            def with_checkpoint_callback(
                self, callback: CheckpointCallback
            ) -> _SpecialisedLoop:
                return _SpecialisedLoop(
                    callback,
                    marker=self.marker,
                    **self.rebuild_controls(),
                )

        clock = FakeClock()
        gate = mock_of[ApprovalGate]()
        original = _SpecialisedLoop(
            marker="kept",
            approval_gate=cast("ApprovalGate", gate),
            stagnation_detector=None,
            compaction_callback=None,
            steering_inbox=None,
            step_classifier=None,
            turn_observer=None,
            clock=clock,
        )

        async def _callback(ctx: AgentContext) -> None:
            del ctx

        rebuilt = original.with_checkpoint_callback(
            cast("CheckpointCallback", _callback)
        )

        assert type(rebuilt) is _SpecialisedLoop
        assert rebuilt.marker == "kept"
        assert rebuilt.approval_gate is gate
        assert rebuilt._clock is clock
        assert rebuilt._checkpoint_callback is _callback
