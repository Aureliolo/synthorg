"""SecurityInterceptionStrategy protocol.

Defines the async interface that the ``ToolInvoker`` calls for
pre-tool security checks and post-tool output scanning.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.security.models import (
        OutputScanResult,
        SecurityContext,
        SecurityVerdict,
    )


@runtime_checkable
class SecurityInterceptionStrategy(Protocol):
    """Protocol for the security interception layer.

    The ``ToolInvoker`` calls ``evaluate_pre_tool`` before execution
    and ``scan_output`` after execution.  Implementations may be
    sync-backed (rule engine) or async (future LLM fallback).
    """

    async def evaluate_pre_tool(
        self,
        context: SecurityContext,
    ) -> SecurityVerdict:
        """Evaluate a tool invocation before execution.

        Args:
            context: The tool call's security context.

        Returns:
            A verdict: allow, deny, or escalate.
        """
        ...

    async def scan_output(
        self,
        context: SecurityContext,
        output: str,
    ) -> OutputScanResult:
        """Scan tool output for sensitive data after execution.

        Args:
            context: The tool call's security context.
            output: The tool's output string.

        Returns:
            An ``OutputScanResult`` with findings and optional redaction.
        """
        ...
