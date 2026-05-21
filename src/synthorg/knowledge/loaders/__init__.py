"""Source loaders for the knowledge substrate.

A :class:`SourceLoader` turns a :class:`KnowledgeSource` into a
:class:`RawDocument` of structural units (pages, files, comments) that
the chunkers consume. Selection is by :class:`SourceType` via
:func:`build_source_loader`.
"""

from synthorg.knowledge.loaders.factory import build_source_loader
from synthorg.knowledge.loaders.protocol import SourceLoader

__all__ = ["SourceLoader", "build_source_loader"]
