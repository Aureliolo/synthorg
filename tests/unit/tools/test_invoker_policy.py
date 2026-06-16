"""Tests for the ToolInvoker runtime policy-engine seam."""

from typing import override

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.providers.models import ToolCall
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.policy_engine.models import (
    PolicyActionRequest,
    PolicyDecision,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


class _PolicyTestTool(BaseTool):
    """Minimal tool used to exercise the policy seam."""

    def __init__(self) -> None:
        super().__init__(
            name="policy_tool",
            description="Test tool for policy evaluation",
            category=ToolCategory.FILE_SYSTEM,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content=f"executed: {arguments.get('cmd', 'x')}")


class _StubPolicyEngine:
    """Deterministic policy engine returning a fixed decision."""

    def __init__(self, *, allow: bool, raises: bool = False) -> None:
        self._allow = allow
        self._raises = raises
        self.calls: list[PolicyActionRequest] = []

    @property
    def name(self) -> str:
        return "stub"

    async def evaluate(self, request: PolicyActionRequest) -> PolicyDecision:
        self.calls.append(request)
        if self._raises:
            msg = "boom"
            raise RuntimeError(msg)
        return PolicyDecision(
            allow=self._allow,
            reason=NotBlankStr("allowed" if self._allow else "denied by stub"),
            latency_ms=0.1,
        )


def _registry() -> ToolRegistry:
    return ToolRegistry([_PolicyTestTool()])


def _call() -> ToolCall:
    return ToolCall(id="call_pol_001", name="policy_tool", arguments={"cmd": "ls"})


async def test_no_policy_engine_is_pass_through() -> None:
    invoker = ToolInvoker(_registry())
    result = await invoker.invoke(_call())
    assert result.is_error is False
    assert result.content == "executed: ls"


async def test_allow_decision_lets_tool_run() -> None:
    engine = _StubPolicyEngine(allow=True)
    invoker = ToolInvoker(
        _registry(),
        policy_engine=engine,
        policy_evaluation_mode="enforce",
    )
    result = await invoker.invoke(_call())
    assert result.is_error is False
    assert result.content == "executed: ls"
    assert len(engine.calls) == 1
    assert engine.calls[0].resource == "policy_tool"


async def test_enforce_deny_blocks_tool() -> None:
    engine = _StubPolicyEngine(allow=False)
    invoker = ToolInvoker(
        _registry(),
        policy_engine=engine,
        policy_evaluation_mode="enforce",
    )
    result = await invoker.invoke(_call())
    assert result.is_error is True
    assert "Policy denied" in result.content


async def test_log_only_deny_proceeds() -> None:
    engine = _StubPolicyEngine(allow=False)
    invoker = ToolInvoker(
        _registry(),
        policy_engine=engine,
        policy_evaluation_mode="log_only",
    )
    result = await invoker.invoke(_call())
    assert result.is_error is False
    assert result.content == "executed: ls"


async def test_evaluation_error_in_enforce_fails_closed() -> None:
    engine = _StubPolicyEngine(allow=False, raises=True)
    invoker = ToolInvoker(
        _registry(),
        policy_engine=engine,
        policy_evaluation_mode="enforce",
    )
    result = await invoker.invoke(_call())
    # An evaluation error in enforce mode must fail CLOSED -- an engine
    # outage cannot silently disable enforcement.
    assert result.is_error is True
    assert "fail-closed" in result.content


async def test_evaluation_error_in_log_only_proceeds() -> None:
    engine = _StubPolicyEngine(allow=False, raises=True)
    invoker = ToolInvoker(
        _registry(),
        policy_engine=engine,
        policy_evaluation_mode="log_only",
    )
    result = await invoker.invoke(_call())
    # In log_only mode an evaluation error is logged and the tool proceeds.
    assert result.is_error is False
    assert result.content == "executed: ls"


async def test_execution_id_is_forwarded_to_policy_request_context() -> None:
    engine = _StubPolicyEngine(allow=True)
    invoker = ToolInvoker(
        _registry(),
        policy_engine=engine,
        policy_evaluation_mode="enforce",
    )
    result = await invoker.invoke(_call(), execution_id="exec_123")
    # The execution_id is security-relevant: Cedar policies target it for
    # fine-grained per-run authorisation, so the seam must thread it through.
    assert result.is_error is False
    assert len(engine.calls) == 1
    assert engine.calls[0].context.get("execution_id") == "exec_123"
