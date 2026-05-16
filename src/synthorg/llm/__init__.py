"""Cross-cutting LLM helpers: model pinning, profile metadata.

The :class:`ModelPinMetadata` model is the source of truth for the
model + sampling parameters a prompt class commits to. Every prompt
class that calls an LLM exposes a ``metadata: ModelPinMetadata``
property so the eval pipeline can reconstruct the exact call shape
without re-reading the class implementation.
"""

from synthorg.llm.metadata import ModelPinMetadata

__all__ = ("ModelPinMetadata",)
