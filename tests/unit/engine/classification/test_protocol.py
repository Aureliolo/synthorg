"""Tests for classification protocol models and abstractions."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from synthorg.budget.coordination_config import (
    DetectionScope,
    DetectorVariant,
    ErrorCategory,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.classification.models import ErrorFinding
from synthorg.engine.classification.protocol import (
    ClassificationSink,
    DetectionContext,
    Detector,
    ScopedContextLoader,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-basic-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _execution_result() -> ExecutionResult:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
    )


@pytest.mark.unit
class TestDetectionScope:
    """DetectionScope enum values."""

    def test_values(self) -> None:
        assert DetectionScope.SAME_TASK.value == "same_task"
        assert DetectionScope.TASK_TREE.value == "task_tree"

    def test_member_count(self) -> None:
        assert len(DetectionScope) == 2


@pytest.mark.unit
class TestDetectorVariant:
    """DetectorVariant enum values."""

    def test_values(self) -> None:
        assert DetectorVariant.HEURISTIC.value == "heuristic"
        assert DetectorVariant.LLM_SEMANTIC.value == "llm_semantic"
        assert DetectorVariant.PROTOCOL_CHECK.value == "protocol_check"
        assert DetectorVariant.BEHAVIOR_CHECK.value == "behavior_check"

    def test_member_count(self) -> None:
        assert len(DetectorVariant) == 4


@pytest.mark.unit
class TestDetectionContext:
    """DetectionContext model validation."""

    def test_same_task_context(self) -> None:
        er = _execution_result()
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.SAME_TASK,
        )
        assert ctx.scope == DetectionScope.SAME_TASK
        assert ctx.agent_id == "agent-1"
        assert ctx.task_id == "task-1"
        assert ctx.delegate_executions == ()
        assert ctx.review_results == ()
        assert ctx.delegation_requests == ()

    def test_task_tree_context(self) -> None:
        er = _execution_result()
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.TASK_TREE,
        )
        assert ctx.scope == DetectionScope.TASK_TREE

    def test_frozen(self) -> None:
        er = _execution_result()
        ctx = DetectionContext(
            execution_result=er,
            agent_id="agent-1",
            task_id="task-1",
            scope=DetectionScope.SAME_TASK,
        )
        with pytest.raises(ValidationError):
            ctx.scope = DetectionScope.TASK_TREE  # type: ignore[misc]

    def test_blank_agent_id_rejected(self) -> None:
        er = _execution_result()
        with pytest.raises(ValidationError):
            DetectionContext(
                execution_result=er,
                agent_id="   ",
                task_id="task-1",
                scope=DetectionScope.SAME_TASK,
            )

    def test_blank_task_id_rejected(self) -> None:
        er = _execution_result()
        with pytest.raises(ValidationError):
            DetectionContext(
                execution_result=er,
                agent_id="agent-1",
                task_id="",
                scope=DetectionScope.SAME_TASK,
            )


@pytest.mark.unit
class TestDetectorProtocol:
    """Detector protocol structural compliance."""

    def test_compliant_class_is_detector(self) -> None:
        """A class with the right shape satisfies Detector."""

        class FakeDetector:
            @property
            def category(self) -> ErrorCategory:
                return ErrorCategory.LOGICAL_CONTRADICTION

            @property
            def supported_scopes(self) -> frozenset[DetectionScope]:
                return frozenset({DetectionScope.SAME_TASK})

            async def detect(
                self,
                context: DetectionContext,
            ) -> tuple[ErrorFinding, ...]:
                return ()

        assert isinstance(FakeDetector(), Detector)

    def test_non_compliant_class_is_not_detector(self) -> None:
        """A class missing detect() does not satisfy Detector."""

        class NotADetector:
            @property
            def category(self) -> ErrorCategory:
                return ErrorCategory.LOGICAL_CONTRADICTION

        assert not isinstance(NotADetector(), Detector)


@pytest.mark.unit
class TestScopedContextLoaderProtocol:
    """ScopedContextLoader protocol structural compliance."""

    def test_compliant_class_is_loader(self) -> None:

        class FakeLoader:
            async def load(
                self,
                execution_result: ExecutionResult,
                agent_id: str,
                task_id: str,
            ) -> DetectionContext:
                return DetectionContext(
                    execution_result=execution_result,
                    agent_id=agent_id,
                    task_id=task_id,
                    scope=DetectionScope.SAME_TASK,
                )

        assert isinstance(FakeLoader(), ScopedContextLoader)


@pytest.mark.unit
class TestClassificationSinkProtocol:
    """ClassificationSink protocol structural compliance."""

    def test_compliant_class_is_sink(self) -> None:

        class FakeSink:
            async def on_classification(self, result: object) -> None:
                pass

        assert isinstance(FakeSink(), ClassificationSink)

    def test_non_compliant_class_is_not_sink(self) -> None:

        class NotASink:
            pass

        assert not isinstance(NotASink(), ClassificationSink)
