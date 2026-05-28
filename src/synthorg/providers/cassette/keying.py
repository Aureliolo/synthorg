"""Deterministic request keying for the provider cassette.

The cassette replays a recorded response when an incoming request
matches a recorded one. "Match" is defined here: a SHA-256 over the
canonical JSON of the request dimensions that determine the provider's
output.

The hash is computed on the **raw** request (never the redacted
human-readable copy), so redaction can scrub the stored cassette file
without ever changing replay-matching behaviour.

Reuses :func:`synthorg.versioning.hashing.compute_content_hash`, the
established canonical-JSON SHA-256 helper, so keying stays consistent
with the rest of the codebase (stable across field order, enum/UUID
representation, and Pydantic dump mode).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    ToolDefinition,
)
from synthorg.versioning.hashing import compute_content_hash


class CassetteMethod(StrEnum):
    """Provider method a cassette interaction was recorded for.

    Part of the request key so the same prompt issued via
    :meth:`complete` versus :meth:`stream` cannot collide on one
    recorded interaction.
    """

    COMPLETE = "complete"
    STREAM = "stream"
    CAPABILITIES = "capabilities"


class CassetteRequestKey(BaseModel):
    """Canonical, hashable description of a provider request.

    Only the dimensions that influence the provider's output are
    included. Response-side / non-deterministic data (latency, retry
    counts injected into ``provider_metadata`` by the base class) is
    deliberately excluded so a replay run keys identically to the
    record run.

    Capability lookups carry only ``method`` + ``provider`` + ``model``;
    ``messages`` / ``tools`` / ``config`` stay at their empty defaults.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: CassetteMethod = Field(description="Provider method")
    provider: NotBlankStr = Field(description="Resolved provider label")
    model: NotBlankStr = Field(description="Model identifier")
    messages: tuple[ChatMessage, ...] = Field(
        default=(),
        description="Conversation history (empty for capability lookups)",
    )
    tools: tuple[ToolDefinition, ...] = Field(
        default=(),
        description="Available tools",
    )
    config: CompletionConfig | None = Field(
        default=None,
        description="Completion parameters; None and an explicit "
        "default config are distinct requests",
    )


def request_hash(  # noqa: PLR0913 -- request dimensions are intrinsic
    *,
    method: CassetteMethod,
    provider: str,
    model: str,
    messages: tuple[ChatMessage, ...] = (),
    tools: tuple[ToolDefinition, ...] = (),
    config: CompletionConfig | None = None,
) -> str:
    """Compute the cassette replay key for a provider request.

    Args:
        method: Provider method the request targets.
        provider: Resolved provider label.
        model: Model identifier.
        messages: Conversation history (empty for capability lookups).
        tools: Available tools.
        config: Optional completion parameters.

    Returns:
        A 64-character lowercase SHA-256 hex digest. Identical request
        inputs always yield the identical digest regardless of dict key
        ordering or Pydantic field definition order.
    """
    key = CassetteRequestKey(
        method=method,
        provider=provider,
        model=model,
        messages=messages,
        tools=tools,
        config=config,
    )
    return compute_content_hash(key)


__all__ = [
    "CassetteMethod",
    "CassetteRequestKey",
    "request_hash",
]
