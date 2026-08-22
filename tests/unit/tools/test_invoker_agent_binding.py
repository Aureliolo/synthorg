# module-kind: tests
"""The pair an agent dispatches on travels into every security decision.

The judge-independence rule compares the evaluator's ``(provider, model)``
against the agent's, and the model half is the one that decides: a connection
does not identify a family, so an aggregator serving two organisations reads as
one family when only the provider travels. Nothing between the loop and the
evaluator re-derives it, so if the invoker drops either half the comparison
runs on an absence and the warning it exists to raise never fires.
"""

from datetime import UTC, datetime
from typing import override
from unittest.mock import AsyncMock

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.types import NotBlankStr
from synthorg.providers.models import ToolCall
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.models import (
    OutputScanResult,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.settings.model_ref import ModelRef
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit

_BINDING = ModelRef(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-capable-001"),
)


class _BoundTestTool(BaseTool):
    """A tool that does nothing, so the context is what is under test."""

    def __init__(self) -> None:
        super().__init__(
            name="bound_tool",
            description="Test tool: bound_tool",
            category=ToolCategory.FILE_SYSTEM,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="done")


def _interceptor() -> AsyncMock:
    """Build an interceptor that allows everything and records what it saw.

    Returns:
        The double.
    """
    interceptor = AsyncMock()
    interceptor.evaluate_pre_tool = AsyncMock(
        return_value=SecurityVerdict(
            verdict=SecurityVerdictType.ALLOW,
            reason="allowed",
            risk_level=ApprovalRiskLevel.LOW,
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=1.0,
        )
    )
    interceptor.scan_output = AsyncMock(return_value=OutputScanResult())
    return interceptor


def _seen(interceptor: AsyncMock) -> SecurityContext:
    """Read the context the interceptor was handed.

    Returns:
        The context.
    """
    interceptor.evaluate_pre_tool.assert_awaited_once()
    context = interceptor.evaluate_pre_tool.await_args.args[0]
    assert isinstance(context, SecurityContext)
    return context


class TestTheBindingReachesTheEvaluator:
    async def test_both_halves_of_the_pair_travel(self) -> None:
        """The model half is the one a family comparison actually needs."""
        interceptor = _interceptor()
        invoker = ToolInvoker(
            ToolRegistry([_BoundTestTool()]),
            security_interceptor=interceptor,
            agent_binding=_BINDING,
        )

        await invoker.invoke(ToolCall(id="c1", name="bound_tool", arguments={}))

        context = _seen(interceptor)
        assert context.agent_provider_name == _BINDING.provider
        assert context.agent_model_id == _BINDING.model_id

    async def test_an_unbound_invoker_states_absence_rather_than_a_placeholder(
        self,
    ) -> None:
        """A stand-in value here would read as a family and compare as one."""
        interceptor = _interceptor()
        invoker = ToolInvoker(
            ToolRegistry([_BoundTestTool()]),
            security_interceptor=interceptor,
        )

        await invoker.invoke(ToolCall(id="c1", name="bound_tool", arguments={}))

        context = _seen(interceptor)
        assert context.agent_provider_name is None
        assert context.agent_model_id is None
