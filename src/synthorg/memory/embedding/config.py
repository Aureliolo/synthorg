# module-kind: declarative
"""Resolved embedder configuration for the memory layer.

Backend-neutral by design. The previous shape was tied to one vendor
SDK, which quietly made "which embedding model does memory use" a
question only that SDK could answer. This model is the resolved answer:
an explicit ``(provider, model)`` pair plus the vector width the store
must be built for.

The explicit provider is not optional padding: under the Explicit
Provider Binding rule every dispatch resolves a concrete
``(provider, model)`` pair, so a bare model name is never enough.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr

# Width of the most common general-purpose embedding models. Only a
# starting point for a caller that has not resolved a real model; the
# store is always built for whatever width is actually resolved.
DEFAULT_EMBEDDING_DIMENSIONS: Final[int] = 1536


class EmbedderConfig(BaseModel):
    """A fully-resolved embedder binding.

    Attributes:
        provider: Embedding provider name. Explicit, never inferred.
        model: Embedding model identifier.
        dims: Vector width. Changing this invalidates every stored
            vector and must be treated as a re-index, never as a widening
            of the existing index.
        dims_explicit: Whether ``dims`` came from an operator override
            rather than the model's catalogued output width. An operator
            who asks for fewer dimensions than the model emits is using
            its Matryoshka representation, so the embedder truncates to
            the requested width; the same mismatch without that intent is
            a misconfiguration and stays a hard failure.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Embedding provider name")
    model: NotBlankStr = Field(description="Embedding model identifier")
    dims: int = Field(
        default=DEFAULT_EMBEDDING_DIMENSIONS,
        ge=1,
        description="Embedding vector dimensions",
    )
    dims_explicit: bool = Field(
        default=False,
        description="Whether dims was set by an operator override",
    )


__all__ = ["DEFAULT_EMBEDDING_DIMENSIONS", "EmbedderConfig"]
