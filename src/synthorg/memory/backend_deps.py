# module-kind: declarative
"""Collaborators a memory backend needs beyond its configuration.

A leaf module on purpose: both :mod:`synthorg.memory.factory` (which
builds backends) and :mod:`synthorg.memory.registry` (which types the
factory signature) need this type, and factory already imports registry.
Defining it in either would make the dependency circular, and hiding the
import behind ``TYPE_CHECKING`` is not an option because typeguard
resolves Protocol annotations at runtime.
"""

from dataclasses import dataclass

from synthorg.core.clock import Clock
from synthorg.memory.embedder_port import TextEmbedder
from synthorg.persistence.memory_vector_protocol import MemoryVectorRepository


@dataclass(frozen=True, slots=True)
class MemoryBackendDeps:
    """Injected collaborators for backend construction.

    Attributes:
        repository: Durable vector store; required by ``sqlvector``.
        embedder: Text embedder. Absent means lexical-only recall, which
            is a real degradation rather than an error, so the backend
            still builds and reports it through ``supports_dense_search``.
        clock: Time source; tests inject a fake.
    """

    repository: MemoryVectorRepository | None = None
    embedder: TextEmbedder | None = None
    clock: Clock | None = None


__all__ = ["MemoryBackendDeps"]
