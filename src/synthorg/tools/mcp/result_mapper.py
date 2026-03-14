"""MCP result mapping (ADR-002 D18).

Pure function that maps MCP raw results to the internal
``ToolExecutionResult`` format used throughout the tool system.
"""

from typing import TYPE_CHECKING, Any

from mcp.types import (
    AudioContent,
    EmbeddedResource,
    ImageContent,
    TextContent,
)

from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_RESULT_ATTACHMENT,
    MCP_RESULT_MAPPED,
    MCP_RESULT_UNKNOWN_BLOCK,
)
from synthorg.tools.base import ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.tools.mcp.models import MCPRawResult

logger = get_logger(__name__)


def map_call_tool_result(raw: MCPRawResult) -> ToolExecutionResult:
    """Map MCP raw result to ToolExecutionResult (ADR-002 D18).

    Mapping rules:
        - TextContent blocks: concatenate into content string.
        - ImageContent: ``"[image: {mimeType}]"`` placeholder +
          base64 in ``metadata["attachments"]``.
        - AudioContent: ``"[audio: {mimeType}]"`` placeholder +
          base64 in ``metadata["attachments"]``.
        - EmbeddedResource: ``"[resource: {uri}]"`` placeholder.
        - structuredContent: ``metadata["structured_content"]``.
        - isError: maps 1:1 to ``is_error``.

    Args:
        raw: Raw MCP result to map.

    Returns:
        Mapped ``ToolExecutionResult``.
    """
    parts: list[str] = []
    attachments: list[dict[str, Any]] = []

    for block in raw.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            parts.append(f"[image: {block.mimeType}]")
            attachments.append(
                {
                    "type": "image",
                    "mimeType": block.mimeType,
                    "data": block.data,
                },
            )
        elif isinstance(block, AudioContent):
            parts.append(f"[audio: {block.mimeType}]")
            attachments.append(
                {
                    "type": "audio",
                    "mimeType": block.mimeType,
                    "data": block.data,
                },
            )
        elif isinstance(block, EmbeddedResource):
            uri = _extract_resource_uri(block)
            parts.append(f"[resource: {uri}]")
        else:
            block_type = type(block).__name__
            logger.warning(
                MCP_RESULT_UNKNOWN_BLOCK,
                unknown_block_type=block_type,
            )
            parts.append(f"[unknown: {block_type}]")

    content = "\n".join(parts) if parts else ""
    metadata: dict[str, Any] = {}

    if attachments:
        metadata["attachments"] = attachments
        logger.debug(
            MCP_RESULT_ATTACHMENT,
            attachment_count=len(attachments),
        )

    if raw.structured_content is not None:
        metadata["structured_content"] = raw.structured_content

    logger.debug(
        MCP_RESULT_MAPPED,
        block_count=len(raw.content),
        has_attachments=bool(attachments),
        has_structured=raw.structured_content is not None,
        is_error=raw.is_error,
    )

    return ToolExecutionResult(
        content=content,
        is_error=raw.is_error,
        metadata=metadata,
    )


def _extract_resource_uri(block: EmbeddedResource) -> str:
    """Extract URI string from an EmbeddedResource block.

    Args:
        block: The embedded resource block.

    Returns:
        The resource URI as a string.
    """
    return str(block.resource.uri)
