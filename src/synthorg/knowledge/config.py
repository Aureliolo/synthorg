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
this union AND :func:`synthorg.knowledge.chunking.factory.build_chunker`
in the same change."""


class KnowledgeConfig(BaseModel):
    """Top-level knowledge-substrate configuration.

    On by default: the substrate is ghost-wired at boot whenever persistence +
    a memory backend exist, and the ``knowledge.enabled`` master switch is
    enforced live per request at the knowledge MCP handlers (read from the
    settings service, not this boot config). The discriminators select pluggable
    strategies via their factories.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description=(
            "Not consulted for the enable decision: the substrate is ghost-wired"
            " at boot and gated live at the handlers via the settings-service"
            " ``knowledge.enabled`` flag. Retained only for config-schema"
            " back-compatibility."
        ),
    )
    pdf_loader: PdfLoaderKind = Field(
        default="pdfplumber",
        description="PDF loader strategy discriminator",
    )
    code_chunker: CodeChunkerKind = Field(
        default="tree_sitter",
        description="Code chunking strategy discriminator",
    )
    repo_root: str = Field(
        default="",
        description=(
            "Operator-configured filesystem root that bounds REPO and PDF"
            " ingestion. A source URI must resolve inside this root or the"
            " ingest is rejected (path-traversal defence). Empty (the"
            " default) is fail-closed: filesystem ingestion is refused"
            " until an operator sets an allowed root."
        ),
    )
