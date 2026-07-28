"""Embedding bindings for the memory substrate.

The operator names the embedding model; this package turns that choice
into a usable binding, measures the model's vector width by asking it, and
provides the built-in embedder for an operator who names it.

Nothing here selects a model, ranks candidates, or substitutes one
embedder for another.
"""

from synthorg.memory.embedding.hashing import (
    BUILTIN_EMBEDDER_DIMS,
    BUILTIN_EMBEDDER_MODEL,
    BUILTIN_EMBEDDER_PROVIDER,
    HashingTextEmbedder,
)
from synthorg.memory.embedding.probe import is_builtin_embedder, probe_embedder_dims
from synthorg.memory.embedding.resolve import resolve_embedder_config

__all__ = [
    "BUILTIN_EMBEDDER_DIMS",
    "BUILTIN_EMBEDDER_MODEL",
    "BUILTIN_EMBEDDER_PROVIDER",
    "HashingTextEmbedder",
    "is_builtin_embedder",
    "probe_embedder_dims",
    "resolve_embedder_config",
]
