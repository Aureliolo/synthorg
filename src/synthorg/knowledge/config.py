"""Configuration for the knowledge substrate.

Carries the pluggable strategy discriminators (PDF loader, code chunker)
and behaviour flags. Chunk budgets and namespace/tag constants live in
:mod:`synthorg.knowledge.constants` because they are part of the
RAG-index contract, not operator-tunable knobs.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PdfLoaderKind = Literal["pdfplumber"]
"""Discriminator for the PDF loader strategy. ``pdfplumber`` (MIT) is the
only shipped implementation; new strategies extend the union and the
loader factory in lockstep (any ``PdfLoaderKind`` value must have a
matching branch in :func:`synthorg.knowledge.loaders.factory.build_source_loader`,
otherwise the wiring fails at startup, not at first ingest)."""

CodeChunkerKind = Literal["tree_sitter"]
"""Discriminator for the code-chunking strategy. ``tree_sitter`` is the
default AST-aware chunker; a future stdlib-``ast`` strategy would extend
this union AND :func:`synthorg.knowledge.chunking.factory.build_code_chunker`
in the same change."""


class KnowledgeConfig(BaseModel):
    """Top-level knowledge-substrate configuration.

    Disabled by default: the substrate is wired only when an operator
    turns it on, mirroring other opt-in subsystems. The discriminators
    select pluggable strategies via their factories.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether the knowledge substrate is wired at startup",
    )
    pdf_loader: PdfLoaderKind = Field(
        default="pdfplumber",
        description="PDF loader strategy discriminator",
    )
    code_chunker: CodeChunkerKind = Field(
        default="tree_sitter",
        description="Code chunking strategy discriminator",
    )
    reranker_enabled: bool = Field(
        default=False,
        description="Whether to apply the LLM query-specific reranker to hits",
    )
