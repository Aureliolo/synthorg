"""Typed args models for the living-doc agent tools.

The body of a write invocation is a discriminated union of one arg
model per block kind (mirroring :data:`DocBlock`). Each arg model owns
its :meth:`to_block` conversion, so the write path needs no central
branch-per-kind validator and the type checker enforces exhaustiveness.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.models import (
    BulletListBlock,
    CodeBlock,
    DecisionBlock,
    DocBlock,
    HeadingBlock,
    LinkBlock,
    MetricBlock,
    ProseBlock,
)

_MIN_HEADING_LEVEL: int = 1
_MAX_HEADING_LEVEL: int = 6

_BLOCK_CONFIG = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class HeadingBlockArg(BaseModel):
    """Heading block in a doc-write invocation."""

    model_config = _BLOCK_CONFIG

    block_kind: Literal["heading"] = "heading"
    level: int = Field(
        ge=_MIN_HEADING_LEVEL,
        le=_MAX_HEADING_LEVEL,
        description="Heading level (1..6)",
    )
    text: NotBlankStr = Field(description="Heading text")

    def to_block(self) -> DocBlock:
        """To block.

        Returns:
            Result of type ``DocBlock``.
        """
        return HeadingBlock(level=self.level, text=self.text)


class ProseBlockArg(BaseModel):
    """Prose paragraph block."""

    model_config = _BLOCK_CONFIG

    block_kind: Literal["prose"] = "prose"
    text: NotBlankStr = Field(description="Prose body")

    def to_block(self) -> DocBlock:
        """To block.

        Returns:
            Result of type ``DocBlock``.
        """
        return ProseBlock(text=self.text)


class BulletListBlockArg(BaseModel):
    """Bulleted-list block."""

    model_config = _BLOCK_CONFIG

    block_kind: Literal["bullet_list"] = "bullet_list"
    items: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Non-empty tuple of bullet entries",
    )

    def to_block(self) -> DocBlock:
        """To block.

        Returns:
            Result of type ``DocBlock``.
        """
        return BulletListBlock(items=self.items)


class CodeBlockArg(BaseModel):
    """Code block."""

    model_config = _BLOCK_CONFIG

    block_kind: Literal["code"] = "code"
    code: str = Field(description="Code body")
    language: NotBlankStr | None = Field(
        default=None,
        description="Renderer language hint",
    )

    def to_block(self) -> DocBlock:
        """To block.

        Returns:
            Result of type ``DocBlock``.
        """
        return CodeBlock(code=self.code, language=self.language)


class DecisionBlockArg(BaseModel):
    """Decision + rationale block."""

    model_config = _BLOCK_CONFIG

    block_kind: Literal["decision"] = "decision"
    decision: NotBlankStr = Field(description="What was decided")
    rationale: NotBlankStr = Field(description="Why this decision")

    def to_block(self) -> DocBlock:
        """To block.

        Returns:
            Result of type ``DocBlock``.
        """
        return DecisionBlock(decision=self.decision, rationale=self.rationale)


class MetricBlockArg(BaseModel):
    """Single-metric block."""

    model_config = _BLOCK_CONFIG

    block_kind: Literal["metric"] = "metric"
    name: NotBlankStr = Field(description="Metric label")
    value: NotBlankStr = Field(description="Metric value (as string)")
    unit: NotBlankStr | None = Field(default=None, description="Optional unit suffix")

    def to_block(self) -> DocBlock:
        """To block.

        Returns:
            Result of type ``DocBlock``.
        """
        return MetricBlock(name=self.name, value=self.value, unit=self.unit)


class LinkBlockArg(BaseModel):
    """Link block."""

    model_config = _BLOCK_CONFIG

    block_kind: Literal["link"] = "link"
    label: NotBlankStr = Field(description="Link display label")
    url: NotBlankStr = Field(description="Link target URL")

    def to_block(self) -> DocBlock:
        """To block.

        Returns:
            Result of type ``DocBlock``.
        """
        return LinkBlock(label=self.label, url=self.url)


WriteLivingDocBlockArg = Annotated[
    HeadingBlockArg
    | ProseBlockArg
    | BulletListBlockArg
    | CodeBlockArg
    | DecisionBlockArg
    | MetricBlockArg
    | LinkBlockArg,
    Field(discriminator="block_kind"),
]
"""Discriminated union over every concrete doc-write block arg."""

_BLOCK_ARG_ADAPTER: TypeAdapter[WriteLivingDocBlockArg] = TypeAdapter(
    WriteLivingDocBlockArg
)


def parse_block_arg(value: object) -> WriteLivingDocBlockArg:
    """Validate one raw block dict against the discriminated union.

    Returns:
        Result of type ``WriteLivingDocBlockArg``.
    """
    return _BLOCK_ARG_ADAPTER.validate_python(value)


class WriteLivingDocArgs(BaseModel):
    """Args for :class:`WriteLivingDocTool`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr = Field(description="Document title")
    doc_type: DocType = Field(description="Doc taxonomy bucket")
    body: tuple[WriteLivingDocBlockArg, ...] = Field(
        min_length=1,
        description="Ordered tuple of body blocks",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Free-form classification tags",
    )
    related_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Task IDs that produced or reference this doc",
    )
    slug: NotBlankStr | None = Field(
        default=None,
        description="Existing slug to update (omit to create a new doc)",
    )


class SearchLivingDocsArgs(BaseModel):
    """Args for :class:`SearchLivingDocsTool`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    query: NotBlankStr = Field(description="Search text")
    doc_types: tuple[DocType, ...] | None = Field(
        default=None,
        description="Optional filter on doc taxonomy buckets",
    )
    limit: int = Field(
        default=8,
        ge=1,
        le=64,
        description="Maximum hits to return",
    )
