"""Output scan result handler — routes sensitive scan results.

Standalone function extracted from ``ToolInvoker`` to keep
``invoker.py`` under the 800-line file limit.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_OUTPUT_REDACTED,
    TOOL_OUTPUT_WITHHELD,
)
from synthorg.security.models import OutputScanResult, ScanOutcome

from .base import ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.providers.models import ToolCall

logger = get_logger(__name__)


def handle_sensitive_scan(
    tool_call: ToolCall,
    result: ToolExecutionResult,
    scan_result: OutputScanResult,
) -> ToolExecutionResult:
    """Route a sensitive scan result to the correct handler.

    Branches on ``ScanOutcome``:

    - ``WITHHELD``: return error with "withheld by policy" message.
    - ``redacted_content`` present: return redacted content.
    - Defensive fallback: withhold output (fail-closed).

    Args:
        tool_call: The tool call being processed.
        result: The original tool execution result.
        scan_result: The scan result with ``has_sensitive_data=True``.

    Returns:
        A new ``ToolExecutionResult`` reflecting the scan outcome.
    """
    if scan_result.outcome == ScanOutcome.WITHHELD:
        logger.warning(
            TOOL_OUTPUT_WITHHELD,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            findings=scan_result.findings,
            note="content withheld by security policy",
        )
        return ToolExecutionResult(
            content=("Sensitive data detected — content withheld by security policy."),
            is_error=True,
            metadata={**result.metadata, "output_withheld": True},
        )
    if scan_result.redacted_content is not None:
        logger.warning(
            TOOL_OUTPUT_REDACTED,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            findings=scan_result.findings,
        )
        return ToolExecutionResult(
            content=scan_result.redacted_content,
            is_error=result.is_error,
            metadata={
                **result.metadata,
                "output_redacted": True,
                "redaction_findings": list(scan_result.findings),
            },
        )
    # Defensive: model_copy() skips model validators, so a policy
    # that clears redacted_content without updating outcome could
    # produce REDACTED with redacted_content=None.  This branch
    # catches that case (and future outcome values) — fail-closed.
    logger.warning(
        TOOL_OUTPUT_WITHHELD,
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        findings=scan_result.findings,
        outcome=scan_result.outcome.value,
        note="no redacted content available — withholding output",
    )
    return ToolExecutionResult(
        content="Sensitive data detected (fail-closed). Tool output withheld.",
        is_error=True,
        metadata={**result.metadata, "output_scan_failed": True},
    )
