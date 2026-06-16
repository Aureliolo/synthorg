"""``ToolInvokerProtocol`` -- the tool-invocation contract the engine depends on.

``engine/loop_protocol.py``, ``engine/react_loop.py``,
``engine/loop_tool_execution.py``, ``engine/agent_engine_context.py``,
and ``engine/loop_helpers.py`` all consume a tool invoker. Typing them
against this protocol lets the engine stay decoupled from the concrete
``synthorg.tools.invoker.ToolInvoker`` implementation.

``ToolInvoker`` satisfies this protocol structurally (it is
``runtime_checkable``); a protocol-level test asserts conformance.
"""

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from synthorg.approval.models import EscalationInfo
from synthorg.core.tool_disclosure import ToolL1Metadata
from synthorg.providers.models import ToolCall, ToolDefinition, ToolResult
from synthorg.tools.registry import ToolRegistry


@runtime_checkable
class ToolInvokerProtocol(Protocol):
    """Validate, authorise, and execute LLM tool calls.

    Implementations populate ``pending_escalations`` during
    ``invoke``/``invoke_all`` when a SecOps ``ESCALATE`` verdict or a
    ``requires_parking`` tool result is observed; the engine's
    ``ApprovalGate`` consults this property after the tool batch to
    decide whether to park the agent context.
    """

    @property
    def registry(self) -> ToolRegistry:
        """Read-only access to the underlying tool registry."""
        ...

    @property
    def pending_escalations(self) -> tuple[EscalationInfo, ...]:
        """Escalations detected during the most recent invoke/invoke_all.

        Implementations reset this tuple at the start of every
        ``invoke``/``invoke_all`` call so callers only ever see the
        escalations from the latest tool batch.  The engine drains the
        tuple between batches via ``ApprovalGate``, so accumulation
        across calls is not required.
        """
        ...

    async def invoke(
        self,
        tool_call: ToolCall,
        *,
        execution_id: str | None = None,
    ) -> ToolResult:
        """Execute a single tool call.

        Recoverable errors are returned as ``ToolResult(is_error=True)``;
        non-recoverable errors (``MemoryError``, ``RecursionError``) are
        re-raised after logging. ``execution_id`` is stamped onto the
        policy-engine context for fine-grained authorization.

        Returns:
            Result of type ``ToolResult``.
        """
        ...

    async def invoke_all(
        self,
        tool_calls: Iterable[ToolCall],
        *,
        max_concurrency: int | None = None,
        execution_id: str | None = None,
    ) -> tuple[ToolResult, ...]:
        """Execute multiple tool calls concurrently.

        Results are returned in input order. ``execution_id`` is stamped
        onto the policy-engine context for fine-grained authorization.

        Returns:
            Tuple of ``ToolResult``.
        """
        ...

    def get_l1_summaries(self) -> tuple[ToolL1Metadata, ...]:
        """Return lightweight L1 metadata for permitted tools.

        Used by the agent engine for system-prompt discovery injection.

        Returns:
            Tuple of ``ToolL1Metadata``.
        """
        ...

    def get_loaded_definitions(
        self,
        loaded_tools: frozenset[str],
    ) -> tuple[ToolDefinition, ...]:
        """Return full definitions for loaded + discovery tools.

        Returns:
            Tuple of ``ToolDefinition``.
        """
        ...
