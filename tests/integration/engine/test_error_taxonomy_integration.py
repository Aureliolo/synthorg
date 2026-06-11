"""Integration tests for the error taxonomy pipeline.

Verifies end-to-end classification with realistic conversation
patterns across all error categories.
"""

from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.budget.coordination_config import (
    DetectionScope,
    DetectorCategoryConfig,
    DetectorVariant,
    ErrorCategory,
    ErrorTaxonomyConfig,
)
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.classification.models import ErrorSeverity
from synthorg.engine.classification.pipeline import classify_execution_errors
from synthorg.engine.classification.sinks import (
    NotificationDispatcherSink,
    PerformanceTrackerSink,
)
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.execution.turn import TurnRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.notifications.models import Notification
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    TokenUsage,
    ToolResult,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.integration


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Integration Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _turn(
    *,
    turn_number: int = 1,
    finish_reason: FinishReason = FinishReason.STOP,
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=100,
        output_tokens=50,
        cost=0.01,
        finish_reason=finish_reason,
    )


def _execution_result(
    messages: tuple[ChatMessage, ...],
    turns: tuple[TurnRecord, ...] = (),
) -> ExecutionResult:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    for msg in messages:
        ctx = ctx.with_message(msg)
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
        turns=turns,
    )


def _taxonomy_config(
    *categories: ErrorCategory,
    enabled: bool = True,
) -> ErrorTaxonomyConfig:
    """Build an ErrorTaxonomyConfig from a list of categories."""
    detectors = {cat: DetectorCategoryConfig() for cat in categories}
    return ErrorTaxonomyConfig(enabled=enabled, detectors=detectors)


@pytest.mark.integration
class TestErrorTaxonomyIntegration:
    """Full pipeline integration with realistic conversation patterns."""

    async def test_contradiction_detected_in_realistic_conversation(
        self,
    ) -> None:
        """A multi-turn conversation with a real contradiction."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a backend developer.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Should we enable caching?",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "Yes, the cache is essential for performance. "
                    "The cache is enabled in our production config."
                ),
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="I checked and it seems off.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "You're right, the cache is not enabled in our "
                    "production config. Let me fix that."
                ),
            ),
        )
        config = _taxonomy_config(ErrorCategory.LOGICAL_CONTRADICTION)
        result = await classify_execution_errors(
            _execution_result(messages, turns=(_turn(), _turn(turn_number=2))),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert result.has_findings
        contradiction_findings = [
            f
            for f in result.findings
            if f.category == ErrorCategory.LOGICAL_CONTRADICTION
        ]
        assert len(contradiction_findings) >= 1

    async def test_tool_error_produces_coordination_failure(self) -> None:
        """Tool execution errors should be classified as coordination failures."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a developer.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Run the tests.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Running the test suite now.",
            ),
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=ToolResult(
                    tool_call_id="call-test-1",
                    content="FAILED: 3 tests failed with ImportError",
                    is_error=True,
                ),
            ),
        )
        turns = (
            _turn(turn_number=1),
            _turn(turn_number=2, finish_reason=FinishReason.ERROR),
        )
        config = _taxonomy_config(ErrorCategory.COORDINATION_FAILURE)
        result = await classify_execution_errors(
            _execution_result(messages, turns=turns),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        assert result.has_findings
        # Should find both the tool error and the error finish reason
        assert result.finding_count >= 2
        for f in result.findings:
            assert f.category == ErrorCategory.COORDINATION_FAILURE
            assert f.severity == ErrorSeverity.HIGH

    async def test_all_categories_run_together(self) -> None:
        """Enable all categories and verify pipeline handles them.

        Without a ``task_repo``, TASK_TREE-only categories
        (delegation protocol, review pipeline) are skipped.
        ``categories_checked`` only contains actually-executed
        categories.
        """
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a developer.",
            ),
            ChatMessage(role=MessageRole.USER, content="Analyze the system."),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="The system processes about 1000 requests per second.",
            ),
            ChatMessage(role=MessageRole.USER, content="Continue."),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="The system processes about 500 requests per second.",
            ),
        )
        config = ErrorTaxonomyConfig(enabled=True)
        result = await classify_execution_errors(
            _execution_result(messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        same_task_categories = {
            ErrorCategory.LOGICAL_CONTRADICTION,
            ErrorCategory.NUMERICAL_DRIFT,
            ErrorCategory.CONTEXT_OMISSION,
            ErrorCategory.COORDINATION_FAILURE,
            ErrorCategory.AUTHORITY_BREACH_ATTEMPT,
        }
        assert set(result.categories_checked) == same_task_categories

    async def test_pipeline_handles_large_conversation(self) -> None:
        """Classification should complete for a moderately large conversation."""
        # Build a moderately large conversation (50 messages)
        messages: list[ChatMessage] = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a developer.",
            ),
        ]
        for i in range(25):
            messages.append(
                ChatMessage(
                    role=MessageRole.USER,
                    content=f"Question {i}.",
                )
            )
            messages.append(
                ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=f"Answer {i}. The SystemManager handles this.",
                )
            )
        turns = tuple(_turn(turn_number=i + 1) for i in range(25))

        config = ErrorTaxonomyConfig(enabled=True)

        result = await classify_execution_errors(
            _execution_result(tuple(messages), turns=turns),
            "agent-1",
            "task-1",
            config=config,
        )

        assert result is not None
        same_task_categories = {
            ErrorCategory.LOGICAL_CONTRADICTION,
            ErrorCategory.NUMERICAL_DRIFT,
            ErrorCategory.CONTEXT_OMISSION,
            ErrorCategory.COORDINATION_FAILURE,
            ErrorCategory.AUTHORITY_BREACH_ATTEMPT,
        }
        assert set(result.categories_checked) == same_task_categories

    async def test_disabled_taxonomy_returns_none(self) -> None:
        """Disabled taxonomy should return None."""
        config = ErrorTaxonomyConfig(enabled=False)
        result = await classify_execution_errors(
            _execution_result(()),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is None

    async def test_numerical_drift_with_realistic_data(self) -> None:
        """Real-world scenario: cost estimate changes between turns."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a cost analyst.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="What will this cost?",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "Based on my analysis, the total cost is "
                    "approximately 5000 USD for the infrastructure."
                ),
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Double check that estimate.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "After recalculating, the total cost is "
                    "approximately 8000 USD for the infrastructure."
                ),
            ),
        )
        config = _taxonomy_config(ErrorCategory.NUMERICAL_DRIFT)
        result = await classify_execution_errors(
            _execution_result(messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        drift_findings = [
            f for f in result.findings if f.category == ErrorCategory.NUMERICAL_DRIFT
        ]
        assert len(drift_findings) >= 1

    async def test_context_omission_with_realistic_data(self) -> None:
        """Entity mentioned early disappears from later discussion."""
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a system architect.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Describe the architecture.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "The architecture has three main components: "
                    "AuthService, PaymentGateway, and NotificationEngine."
                ),
            ),
            ChatMessage(role=MessageRole.USER, content="Tell me more."),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "AuthService handles JWT tokens and session management. "
                    "PaymentGateway processes payment webhooks."
                ),
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="What about deployment?",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "We deploy AuthService to a dedicated cluster. "
                    "The database runs on a managed relational store."
                ),
            ),
            ChatMessage(role=MessageRole.USER, content="And monitoring?"),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=(
                    "We use a metrics collector for metrics "
                    "and a dashboard service for dashboards. "
                    "Alerts go to the ops team."
                ),
            ),
        )
        config = _taxonomy_config(ErrorCategory.CONTEXT_OMISSION)
        result = await classify_execution_errors(
            _execution_result(messages, turns=(_turn(),)),
            "agent-1",
            "task-1",
            config=config,
        )
        assert result is not None
        # NotificationEngine and PaymentGateway should be flagged
        # as they disappear from later messages
        omitted_descriptions = " ".join(f.description for f in result.findings)
        assert "NotificationEngine" in omitted_descriptions


# ── Integration tests for the error-taxonomy acceptance criteria ──


def _child_task(
    *,
    task_id: str,
    parent_task_id: str,
    assigned_to: str,
    description: str = "Sub-task",
) -> Task:
    return Task(
        id=as_uuid(task_id),
        title=f"Child {task_id}",
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="integration-test",
        created_by="agent-root",
        assigned_to=assigned_to,
        status=TaskStatus.IN_PROGRESS,
        parent_task_id=parent_task_id,
        delegation_chain=("agent-root",),
        estimated_complexity=Complexity.MEDIUM,
    )


@pytest.mark.integration
class TestCrossAgentNumericalDriftTaskTree:
    """LLM numerical-drift detection across a task tree.

    Covers the acceptance criterion "classify a task tree with
    multiple agents producing cross-agent numerical drift,
    verify the LLM detector catches it".
    """

    async def test_semantic_detector_runs_at_task_tree_scope(self) -> None:
        """A TASK_TREE LLM detector sees the enriched context.

        This test builds a parent task execution, stubs the task
        repository to return two child tasks delegated to other
        agents (simulating a task tree), enables the semantic
        numerical drift detector at TASK_TREE scope, and wires a
        mock LLM provider that returns a finding.  The assertion
        is that the finding flows through the pipeline -- i.e. the
        LLM detector was invoked with a TASK_TREE context and its
        output reached the ClassificationResult, not that a real
        LLM was called.
        """
        parent_conversation = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are the lead engineer.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Estimate the migration effort.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Lead estimate: 120 hours.",
            ),
        )

        child_alpha = _child_task(
            task_id="child-alpha",
            parent_task_id="task-root",
            assigned_to="agent-alpha",
            description="Delegate alpha reported 40 hours.",
        )
        child_beta = _child_task(
            task_id="child-beta",
            parent_task_id="task-root",
            assigned_to="agent-beta",
            description="Delegate beta reported 240 hours.",
        )

        mock_repo = AsyncMock()
        mock_repo.query = AsyncMock(return_value=(child_alpha, child_beta))

        provider_response = CompletionResponse(
            content=(
                '[{"description": "Numerical estimate diverges between '
                'agents (lead: 120h, alpha: 40h, beta: 240h)", '
                '"severity": "high", '
                '"evidence": ["lead=120", "alpha=40", "beta=240"], '
                '"turn_start": 0, "turn_end": 2}]'
            ),
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=50,
                output_tokens=50,
                cost=0.0005,
            ),
            model="test-small-001",
        )
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value=provider_response)

        config = ErrorTaxonomyConfig(
            enabled=True,
            detectors={
                ErrorCategory.NUMERICAL_DRIFT: DetectorCategoryConfig(
                    variants=(DetectorVariant.LLM_SEMANTIC,),
                    scope=DetectionScope.TASK_TREE,
                ),
            },
            classification_budget_per_task=0.01,
        )

        result = await classify_execution_errors(
            _execution_result(
                parent_conversation,
                turns=(_turn(),),
            ),
            "agent-root",
            "task-root",
            config=config,
            task_repo=mock_repo,
            provider=mock_provider,
        )

        assert result is not None
        mock_provider.complete.assert_awaited_once()

        # Verify the prompt sent to the provider includes the
        # parent conversation content (proving the context loader
        # and _build_conversation_text reached the LLM). The
        # transcript is wrapped in <task-data>...</task-data> fences
        # by ``wrap_untrusted``.
        call_args = mock_provider.complete.call_args
        prompt_messages = call_args[0][0]  # first positional arg
        system_prompt = prompt_messages[0].content
        assert "<task-data>" in system_prompt
        assert "</task-data>" in system_prompt
        # The parent agent's estimate should be in the prompt.
        assert "120" in system_prompt

        drift_findings = [
            f for f in result.findings if f.category == ErrorCategory.NUMERICAL_DRIFT
        ]
        assert len(drift_findings) >= 1
        # The LLM-sourced finding must be present (HIGH severity,
        # evidence referring to cross-agent estimates).
        llm_findings = [f for f in drift_findings if f.severity == ErrorSeverity.HIGH]
        assert llm_findings, "Expected at least one HIGH-severity LLM drift finding"
        evidence_joined = " ".join(" ".join(f.evidence) for f in llm_findings)
        assert "lead" in evidence_joined
        assert "alpha" in evidence_joined


@pytest.mark.integration
class TestDelegationProtocolViolationIntegration:
    """End-to-end delegation protocol violation detection.

    Covers the acceptance criterion "classify a task with a
    delegation protocol violation, verify the detector catches it".
    """

    async def test_delegation_protocol_pipeline_executes_at_task_tree(
        self,
    ) -> None:
        """The delegation detector runs end-to-end with legal fixtures.

        Validates that the pipeline accepts the TASK_TREE config
        for ``DelegationProtocolDetector``, the loader calls the
        task repository, and the detector reports zero findings
        when all tasks have legal delegation chains.  The
        positive-path violation assertion lives in
        :meth:`test_delegation_protocol_runs_at_task_tree_scope`
        because ``Task`` model validation prevents the circular
        delegation case from being constructed via the loader.
        """
        legal_child = Task(
            id=as_uuid("child-legal"),
            title="Legal delegation",
            description="Normal child task",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="integration-test",
            created_by="agent-root",
            assigned_to="agent-delegate",
            status=TaskStatus.IN_PROGRESS,
            parent_task_id="task-root",
            delegation_chain=("agent-root",),
            estimated_complexity=Complexity.MEDIUM,
        )
        mock_repo = AsyncMock()
        mock_repo.query = AsyncMock(return_value=(legal_child,))

        config = ErrorTaxonomyConfig(
            enabled=True,
            detectors={
                ErrorCategory.DELEGATION_PROTOCOL_VIOLATION: DetectorCategoryConfig(
                    variants=(DetectorVariant.PROTOCOL_CHECK,),
                    scope=DetectionScope.TASK_TREE,
                ),
            },
        )

        result = await classify_execution_errors(
            _execution_result(
                (
                    ChatMessage(
                        role=MessageRole.SYSTEM,
                        content="You are the root agent.",
                    ),
                    ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content="Dispatching sub-task.",
                    ),
                ),
                turns=(_turn(),),
            ),
            "agent-root",
            "task-root",
            config=config,
            task_repo=mock_repo,
        )

        assert result is not None
        mock_repo.query.assert_awaited_once()
        delegation_findings = [
            f
            for f in result.findings
            if f.category == ErrorCategory.DELEGATION_PROTOCOL_VIOLATION
        ]
        assert delegation_findings == []  # legal fixture, no findings

    async def test_delegation_protocol_runs_at_task_tree_scope(self) -> None:
        """Direct protocol-check via DelegationProtocolDetector.

        Bypasses the task-tree loader BFS (which would filter out
        the orphan task) and calls the detector directly to prove
        it catches the ``parent_task_id is None`` case end-to-end
        when such a record reaches it.  This is the closest
        guaranteed path to exercising the HIGH finding given
        ``Task`` model constraints that forbid
        ``assigned_to in delegation_chain``.
        """
        from synthorg.communication.delegation.models import DelegationRequest
        from synthorg.engine.classification.protocol import DetectionContext
        from synthorg.engine.classification.protocol_detectors import (
            DelegationProtocolDetector,
        )

        orphan = Task(
            id=as_uuid("child-orphan"),
            title="Orphan delegation",
            description="Delegated without parent linkage",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="integration-test",
            created_by="agent-root",
            assigned_to="agent-delegate",
            status=TaskStatus.IN_PROGRESS,
            parent_task_id=None,
            delegation_chain=("agent-root",),
            estimated_complexity=Complexity.MEDIUM,
        )
        request = DelegationRequest(
            delegator_id="agent-root",
            delegatee_id="agent-delegate",
            task=orphan,
            refinement="Please handle this.",
        )
        context = DetectionContext(
            execution_result=_execution_result((), turns=(_turn(),)),
            agent_id="agent-root",
            task_id="task-root",
            scope=DetectionScope.TASK_TREE,
            delegation_requests=(request,),
        )
        findings = await DelegationProtocolDetector().detect(context)
        assert len(findings) >= 1
        assert any(
            f.category == ErrorCategory.DELEGATION_PROTOCOL_VIOLATION for f in findings
        )
        assert all(f.severity == ErrorSeverity.HIGH for f in findings)
        assert any("parent_task_id" in f.description for f in findings)


@pytest.mark.integration
class TestClassificationSinksFlowThrough:
    """Findings flow from the pipeline to configured sinks.

    Covers the acceptance criterion "findings flow through to
    the performance tracker and notification dispatcher".
    """

    async def test_findings_reach_performance_tracker_and_dispatcher(
        self,
        mock_dispatcher: AsyncMock,
    ) -> None:
        """Both sinks must see the generated findings.

        Uses a live ``PerformanceTracker`` (in-memory) and spies on
        its ``record_collaboration_event`` method, plus the shared
        ``mock_dispatcher`` fixture (an ``AsyncMock`` covering the full
        ``NotificationDispatcher`` interface) so we can assert on
        ``dispatch`` calls without pulling in a real sink.
        """
        tracker = PerformanceTracker()
        record_spy = AsyncMock(wraps=tracker.record_collaboration_event)
        tracker.record_collaboration_event = record_spy  # type: ignore[method-assign]

        dispatcher = mock_dispatcher

        performance_sink = PerformanceTrackerSink(tracker=tracker)
        notification_sink = NotificationDispatcherSink(
            dispatcher=dispatcher,
            min_severity=ErrorSeverity.HIGH,
            max_events_per_window=10,
            window_seconds=60.0,
        )

        # A realistic coordination-failure conversation: a tool
        # error plus an ERROR finish reason produces two HIGH
        # findings that should fan out to both sinks.
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are an engineer.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Run the build.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Running the build now.",
            ),
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=ToolResult(
                    tool_call_id="call-build-1",
                    content="FAILED: build error",
                    is_error=True,
                ),
            ),
        )
        turns = (
            _turn(turn_number=1),
            _turn(turn_number=2, finish_reason=FinishReason.ERROR),
        )
        config = _taxonomy_config(ErrorCategory.COORDINATION_FAILURE)

        result = await classify_execution_errors(
            _execution_result(messages, turns=turns),
            "agent-sink-test",
            "task-sink-test",
            config=config,
            sinks=(performance_sink, notification_sink),
        )

        assert result is not None
        assert result.has_findings
        finding_count = result.finding_count
        assert finding_count >= 1

        # Performance tracker received one collaboration event
        # per finding.
        assert record_spy.await_count == finding_count

        # Notification dispatcher was invoked for HIGH findings.
        assert dispatcher.dispatch.await_count >= 1
        dispatched: list[Notification] = [
            call.args[0] for call in dispatcher.dispatch.await_args_list
        ]
        assert all(n.source == "engine.classification" for n in dispatched)
        expected_title = "coordination_failure"
        assert all(expected_title in n.title.lower() for n in dispatched)

    async def test_notification_rate_limiter_caps_alert_storms(
        self,
        mock_dispatcher: AsyncMock,
    ) -> None:
        """Per-agent rate limiter drops excess notifications in the window."""
        dispatcher = mock_dispatcher

        # A deterministic clock so the sliding window behaves
        # predictably during a single-shot classification run.
        fake_time = [0.0]
        sink = NotificationDispatcherSink(
            dispatcher=dispatcher,
            min_severity=ErrorSeverity.HIGH,
            max_events_per_window=1,
            window_seconds=60.0,
            clock=lambda: fake_time[0],
        )

        # Build a result with 3 HIGH-severity findings for the
        # same agent.  Only the first should dispatch.
        messages = (
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are an engineer.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content="Run the build.",
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Running.",
            ),
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=ToolResult(
                    tool_call_id="call-build-1",
                    content="FAILED: 1",
                    is_error=True,
                ),
            ),
            ChatMessage(
                role=MessageRole.TOOL,
                tool_result=ToolResult(
                    tool_call_id="call-build-2",
                    content="FAILED: 2",
                    is_error=True,
                ),
            ),
        )
        turns = (
            _turn(turn_number=1),
            _turn(turn_number=2, finish_reason=FinishReason.ERROR),
        )
        config = _taxonomy_config(ErrorCategory.COORDINATION_FAILURE)
        result = await classify_execution_errors(
            _execution_result(messages, turns=turns),
            "agent-storm",
            "task-storm",
            config=config,
            sinks=(sink,),
        )

        assert result is not None
        assert result.finding_count >= 2
        # Regardless of how many findings were produced, only one
        # notification survives the 1/60s rate limit.
        assert dispatcher.dispatch.await_count == 1
