"""Tests that the invoker opens a cost-recording scope for cost-billing tools.

A tool declaring ``cost_scope_category`` (e.g. image generation) must run
inside a ``cost_recording_scope`` so provider cost incurred during the tool
call is attributed to the invoker's agent/task, mirroring chat completions.
"""

from typing import ClassVar, override

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.config import BudgetConfig
from synthorg.budget.tracker import CostTracker
from synthorg.providers.cost_recording import CostRecordingContext, current_cost_context
from synthorg.providers.models import ToolCall
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


class _CostProbeTool(BaseTool):
    """Captures the ambient cost context observed during ``execute``."""

    cost_scope_category: ClassVar[LLMCallCategory | None] = (
        LLMCallCategory.IMAGE_GENERATION
    )

    def __init__(self) -> None:
        super().__init__(name="cost_probe", category=ToolCategory.DESIGN)
        self.seen: CostRecordingContext | None = None

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        self.seen = current_cost_context()
        return ToolExecutionResult(content="ok")


class _PlainTool(BaseTool):
    """A tool that bills no provider cost (no ``cost_scope_category``)."""

    def __init__(self) -> None:
        super().__init__(name="plain", category=ToolCategory.DESIGN)
        self.seen: CostRecordingContext | None = None

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        self.seen = current_cost_context()
        return ToolExecutionResult(content="ok")


def _invoker(tool: BaseTool) -> ToolInvoker:
    return ToolInvoker(
        ToolRegistry([tool]),
        agent_id="agent-1",
        task_id="task-1",
        cost_tracker=CostTracker(budget_config=BudgetConfig()),
    )


async def test_cost_billing_tool_runs_in_scope() -> None:
    tool = _CostProbeTool()
    await _invoker(tool).invoke(ToolCall(id="c1", name="cost_probe", arguments={}))
    assert tool.seen is not None
    assert tool.seen.call_category is LLMCallCategory.IMAGE_GENERATION
    assert tool.seen.agent_id == "agent-1"
    assert tool.seen.task_id == "task-1"


async def test_plain_tool_runs_without_scope() -> None:
    tool = _PlainTool()
    await _invoker(tool).invoke(ToolCall(id="c1", name="plain", arguments={}))
    assert tool.seen is None


async def test_no_scope_without_cost_tracker() -> None:
    tool = _CostProbeTool()
    invoker = ToolInvoker(
        ToolRegistry([tool]),
        agent_id="agent-1",
        task_id="task-1",
    )
    await invoker.invoke(ToolCall(id="c1", name="cost_probe", arguments={}))
    assert tool.seen is None
