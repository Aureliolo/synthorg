# module-kind: code
"""Execution-loop construction for the agent engine.

Sibling of :mod:`synthorg.engine.agent_engine_factories`, which owns the
approval-gate, security and tool-invoker factories. Loop selection is its own
question with its own dependencies, and separating it keeps each module inside
its size budget.
"""

from synthorg.core.task import Task
from synthorg.engine._agent_loop_selection import resolve_loop
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.loop_protocol import ExecutionLoop
from synthorg.engine.loop_selector import AutoLoopConfig, build_execution_loop
from synthorg.engine.openhands.config import (
    OpenHandsLoopConfig,
    OpenHandsLoopDeps,
)
from synthorg.engine.quality.classifier import StepQualityClassifier
from synthorg.engine.stagnation.protocol import StagnationDetector
from synthorg.observability import get_logger

logger = get_logger(__name__)


class AgentEngineLoopFactoriesMixin:
    """Mixin providing the engine's execution-loop factories."""

    _approval_gate: ApprovalGate | None
    _stagnation_detector: StagnationDetector | None
    _step_classifier: StepQualityClassifier | None
    _compaction_callback: CompactionCallback | None
    _steering_inbox: SteeringInbox | None
    _auto_loop_config: AutoLoopConfig | None
    _loop: ExecutionLoop
    _openhands_loop_config: OpenHandsLoopConfig | None
    _openhands_loop_deps: OpenHandsLoopDeps | None

    def _make_default_loop(self) -> ExecutionLoop:
        """Build the default ``react`` loop via the shared factory.

        Returns:
            A freshly-built ReAct :class:`ExecutionLoop` wired with
            this engine's approval gate, stagnation detector, and
            compaction callback.
        """
        return build_execution_loop(
            "react",
            approval_gate=self._approval_gate,
            stagnation_detector=self._stagnation_detector,
            compaction_callback=self._compaction_callback,
            steering_inbox=self._steering_inbox,
            step_classifier=self._step_classifier,
        )

    async def _resolve_loop(
        self,
        task: Task,
        agent_id: str = "",
        task_id: str = "",
    ) -> ExecutionLoop:
        """Select the execution loop for a task.

        Returns:
            The configured default loop when auto-selection is off;
            otherwise an :class:`ExecutionLoop` of the type selected
            from task complexity.
        """
        return await resolve_loop(
            task,
            agent_id=agent_id,
            task_id=task_id,
            static_loop=self._loop,
            auto_loop_config=self._auto_loop_config,
            build=self._build_loop,
        )

    def _build_loop(self, loop_type: str) -> ExecutionLoop:
        """Build a loop of ``loop_type`` from the engine's dependencies.

        Returns:
            The constructed :class:`ExecutionLoop`.
        """
        return build_execution_loop(
            loop_type,
            approval_gate=self._approval_gate,
            stagnation_detector=self._stagnation_detector,
            compaction_callback=self._compaction_callback,
            openhands_loop_config=self._openhands_loop_config,
            openhands_loop_deps=self._openhands_loop_deps,
            steering_inbox=self._steering_inbox,
            step_classifier=self._step_classifier,
        )


__all__ = ["AgentEngineLoopFactoriesMixin"]
