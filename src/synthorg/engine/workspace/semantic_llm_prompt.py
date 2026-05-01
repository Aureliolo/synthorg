"""Prompt building and response parsing for LLM-based semantic analysis.

Pure functions that construct messages, tool definitions, and parse
LLM responses into ``MergeConflict`` instances.
"""

import json
import re
from typing import Any

from synthorg.core.enums import ConflictType
from synthorg.engine.prompt_safety import (
    TAG_CODE_DIFF,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.engine.workspace.models import MergeConflict
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    ToolDefinition,
)

logger = get_logger(__name__)

_TOOL_NAME = "submit_semantic_review"

_MARKDOWN_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n(.*?)\n\s*```",
    re.DOTALL,
)


def build_semantic_review_tool() -> ToolDefinition:
    """Build the ``submit_semantic_review`` tool definition.

    Returns:
        A ToolDefinition with JSON Schema for semantic conflict reports.
    """
    conflict_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path of the file with the conflict",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear description of the semantic conflict, "
                    "including what was changed and what breaks"
                ),
            },
        },
        "required": ["file_path", "description"],
    }

    return ToolDefinition(
        name=_TOOL_NAME,
        description=(
            "Submit the semantic review results. Report any logical "
            "conflicts found in the merged code that would cause "
            "runtime errors or incorrect behavior."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "conflicts": {
                    "type": "array",
                    "items": conflict_schema,
                    "description": (
                        "List of semantic conflicts found. "
                        "Empty list if no conflicts detected."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "Brief summary of the review",
                },
            },
            "required": ["conflicts", "summary"],
        },
    )


def build_system_message() -> ChatMessage:
    """Build the system prompt for semantic review.

    The system prompt carries an explicit directive that
    ``<code-diff>`` fences wrap untrusted code content so the LLM
    does not execute instructions embedded in reviewed source.

    Returns:
        System message with review instructions.
    """
    return ChatMessage(
        role=MessageRole.SYSTEM,
        content=(
            "You are a code review agent specializing in detecting "
            "semantic conflicts after git merges. You analyze merged "
            "code to find logical issues that textual merge cannot "
            "detect.\n\n"
            "Focus on:\n"
            "1. References to functions, classes, or variables that "
            "were renamed or removed\n"
            "2. Function signature changes that break callers\n"
            "3. Duplicate definitions that shadow each other\n"
            "4. Broken imports of removed exports\n"
            "5. Logic errors from independently correct changes that "
            "conflict semantically\n\n"
            "Only report REAL conflicts that would cause runtime "
            "errors or incorrect behavior. Do NOT report style issues, "
            "naming conventions, or potential improvements.\n\n"
            "Use the submit_semantic_review tool to report your findings.\n\n"
            + untrusted_content_directive((TAG_CODE_DIFF,))
        ),
    )


def build_review_message(
    *,
    diff_summary: str,
    changed_files: dict[str, str],
) -> ChatMessage:
    """Build the user message containing code for review.

    Args:
        diff_summary: Git diff summary of the merge.
        changed_files: Mapping of file path to merged content.

    Returns:
        User message with code context for review.
    """
    # Every attacker-influenced string (diff summary, the file
    # paths in ``changed_files``, and the file contents themselves)
    # must sit inside a ``<code-diff>`` fence. Without the wrap, a
    # malicious filename or diff-summary line could masquerade as
    # operator framing and escape the review intent.
    parts = [
        "Review the following merged code for semantic conflicts.\n\n",
        "## Diff Summary\n",
        wrap_untrusted(TAG_CODE_DIFF, diff_summary),
        "\n\n## Merged File Contents\n",
    ]

    for path, content in changed_files.items():
        parts.append("\n### File path\n")
        parts.append(wrap_untrusted(TAG_CODE_DIFF, path))
        parts.append("\n\n### Contents\n")
        parts.append(wrap_untrusted(TAG_CODE_DIFF, content))
        parts.append("\n")

    return ChatMessage(
        role=MessageRole.USER,
        content="".join(parts),
    )


def parse_tool_call_response(
    response: CompletionResponse,
) -> tuple[MergeConflict, ...]:
    """Parse semantic conflicts from a tool call response.

    Args:
        response: LLM completion response with tool calls.

    Returns:
        Tuple of MergeConflict instances from the response.

    Raises:
        ValueError: When the response cannot be parsed.
    """
    if response.tool_calls:
        for tc in response.tool_calls:
            if tc.name == _TOOL_NAME:
                return _parse_conflicts_from_args(tc.arguments)

    # Fallback: try parsing from content
    if response.content:
        return _parse_conflicts_from_content(response.content)

    msg = "No tool call or parseable content in response"
    logger.warning(WORKSPACE_SEMANTIC_ANALYSIS_FAILED, reason="no_tool_call")
    raise ValueError(msg)


def _parse_conflicts_from_args(
    arguments: dict[str, Any],
) -> tuple[MergeConflict, ...]:
    """Parse conflicts from tool call arguments dict."""
    raw_conflicts = arguments.get("conflicts", [])
    if not isinstance(raw_conflicts, list):
        msg = f"Expected list of conflicts, got {type(raw_conflicts).__name__}"
        logger.warning(
            WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            reason="invalid_conflicts_type",
            error=msg,
        )
        raise ValueError(msg)  # noqa: TRY004 -- must be ValueError for retry loop

    conflicts: list[MergeConflict] = []
    for item in raw_conflicts:
        if not isinstance(item, dict):
            logger.warning(
                WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                reason="unexpected_conflict_item_type",
                item_type=type(item).__name__,
            )
            continue
        file_path = item.get("file_path", "")
        description = item.get("description", "")
        if not file_path or not description:
            logger.warning(
                WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
                reason="incomplete_conflict_item",
                file_path=file_path or "(empty)",
                has_description=bool(description),
            )
            continue
        conflicts.append(
            MergeConflict(
                file_path=file_path,
                conflict_type=ConflictType.SEMANTIC,
                description=description,
            ),
        )
    return tuple(conflicts)


def _parse_conflicts_from_content(content: str) -> tuple[MergeConflict, ...]:
    """Try to parse conflicts from content text (JSON fallback)."""
    match = _MARKDOWN_FENCE_RE.search(content)
    text = match.group(1) if match else content

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"Cannot parse response content as JSON: {exc}"
        logger.warning(
            WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
            reason="parse_error",
            error=msg,
        )
        raise ValueError(msg) from exc

    if isinstance(data, dict):
        return _parse_conflicts_from_args(data)
    if isinstance(data, list):
        return _parse_conflicts_from_args({"conflicts": data})

    msg = f"Unexpected JSON structure: {type(data).__name__}"
    logger.warning(
        WORKSPACE_SEMANTIC_ANALYSIS_FAILED,
        reason="unexpected_json",
        error=msg,
    )
    raise ValueError(msg)
