"""Shared test helpers for hybrid loop tests.

Extracted to keep individual test files under 800 lines.
"""

import json
from typing import override

from pydantic import JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.plan_loop_context import StepRunContext, StepRunState
from synthorg.engine.plan_models import ExecutionPlan, PlanStep
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry


def _usage(
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> TokenUsage:
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=0.001,
    )


def _plan_response(steps: list[dict[str, JsonValue]]) -> CompletionResponse:
    """Build a plan response with JSON-formatted steps."""
    plan = {"steps": steps}
    return CompletionResponse(
        content=json.dumps(plan),
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


def _single_step_plan() -> CompletionResponse:
    return _plan_response(
        [
            {
                "step_number": 1,
                "description": "Analyze and solve the problem",
                "expected_outcome": "Problem solved",
            },
        ]
    )


def _multi_step_plan() -> CompletionResponse:
    return _plan_response(
        [
            {
                "step_number": 1,
                "description": "Research the topic",
                "expected_outcome": "Understanding gained",
            },
            {
                "step_number": 2,
                "description": "Implement solution",
                "expected_outcome": "Code written",
            },
            {
                "step_number": 3,
                "description": "Verify results",
                "expected_outcome": "Tests pass",
            },
        ]
    )


def _stop_response(content: str = "Done.") -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


def _summary_response(
    *,
    replan: bool = False,
    summary: str = "Step completed successfully.",
) -> CompletionResponse:
    """Build a progress-summary response."""
    return CompletionResponse(
        content=json.dumps({"summary": summary, "replan": replan}),
        finish_reason=FinishReason.STOP,
        usage=_usage(),
        model="test-model-001",
    )


def _tool_use_response(
    tool_name: str = "echo",
    tool_call_id: str = "tc-1",
) -> CompletionResponse:
    return CompletionResponse(
        content=None,
        tool_calls=(ToolCall(id=tool_call_id, name=tool_name, arguments={}),),
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


def _content_filter_response() -> CompletionResponse:
    return CompletionResponse(
        content=None,
        finish_reason=FinishReason.CONTENT_FILTER,
        usage=_usage(),
        model="test-model-001",
    )


def _step_fail_response() -> CompletionResponse:
    """Response causing step failure (TOOL_USE with no tool calls)."""
    return CompletionResponse(
        content="I could not complete this step.",
        finish_reason=FinishReason.TOOL_USE,
        usage=_usage(),
        model="test-model-001",
    )


class _StubTool(BaseTool):
    def __init__(self, name: str = "echo") -> None:
        super().__init__(
            name=name,
            description="Test tool",
            category=ToolCategory.CODE_EXECUTION,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=f"echoed: {arguments}",
            is_error=False,
        )


def _make_invoker(*tool_names: str) -> ToolInvoker:
    tools = [_StubTool(name=n) for n in tool_names]
    return ToolInvoker(ToolRegistry(tools))


def _ctx_with_user_msg(ctx: AgentContext) -> AgentContext:
    msg = ChatMessage(role=MessageRole.USER, content="Do something")
    return ctx.with_message(msg)


def _make_plan_model() -> ExecutionPlan:
    """Build an ExecutionPlan model for direct helper tests."""
    return ExecutionPlan(
        steps=(
            PlanStep(
                step_number=1,
                description="Research the topic",
                expected_outcome="Understanding gained",
            ),
            PlanStep(
                step_number=2,
                description="Implement solution",
                expected_outcome="Code written",
            ),
        ),
        original_task_summary="test task",
    )


def _step_run_context(
    provider: CompletionProvider,
    *,
    executor_model: str = "example-medium-001",
    planner_model: str = "example-large-001",
) -> StepRunContext:
    """Build a StepRunContext for a helper called outside a loop.

    Returns:
        A context wired to *provider* with distinct executor and planner
        model ids, so a test asserting on which model a call used cannot
        pass by accident.
    """
    return StepRunContext(
        provider=provider,
        executor_model=executor_model,
        planner_model=planner_model,
        completion_config=CompletionConfig(),
    )


def _step_run_state(
    ctx: AgentContext,
    plan: ExecutionPlan,
) -> StepRunState:
    """Build a StepRunState with its own fresh accumulators.

    Returns:
        A state whose ``turns`` and ``all_plans`` are new lists, so a helper
        driven directly cannot append into another test's history.
    """
    return StepRunState(ctx=ctx, plan=plan, turns=[], all_plans=[])
