"""``WriteLivingDocTool`` -- agents author / update living documents.

The tool reads ``project_id`` from the agent execution context (passed
to ``execute()`` via the optional ``context`` kwarg the invoker
forwards). Cross-project authority is checked by callers via the
:class:`TrustService` seam, not by the tool itself.
"""

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel  # noqa: TC002 -- ClassVar runtime ref

from synthorg.core.enums import (
    ActionType,
    ToolCategory,
)
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
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docs import (
    DOC_WRITE_FAILED,
    DOC_WRITTEN,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.docs._args import (
    WriteLivingDocArgs,
    WriteLivingDocBlockArg,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.docs_engine.service import DocsService

logger = get_logger(__name__)


class WriteLivingDocTool(BaseTool):
    """Agent tool that creates or updates a living document.

    Args:
        docs_service: Engine entry point used to persist the doc.
        project_id: Active project for this tool instance. Wired by
            the per-task tool factory from the execution context.
        author_agent_id: The calling agent's identifier; embedded into
            the doc metadata so the wiki can show attribution.
    """

    args_model: ClassVar[type[BaseModel] | None] = WriteLivingDocArgs

    def __init__(
        self,
        *,
        docs_service: DocsService,
        project_id: NotBlankStr,
        author_agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name="write_living_doc",
            description=(
                "Create or update a living document (status report, "
                "deliverable, or knowledge note) for the active project. "
                "Pass a structured body of typed blocks; the service "
                "derives a stable slug from the title."
            ),
            parameters_schema=WriteLivingDocArgs.model_json_schema(),
            category=ToolCategory.OTHER,
            action_type=ActionType.DOCS_WRITE.value,
        )
        self._docs_service = docs_service
        self._project_id = project_id
        self._author_agent_id = author_agent_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Translate args to a :class:`LivingDocument` body and persist."""
        try:
            parsed = WriteLivingDocArgs.model_validate(arguments)
            body = _materialise_body(parsed.body)
            metadata = await self._docs_service.write_doc(
                project_id=self._project_id,
                title=parsed.title,
                doc_type=parsed.doc_type,
                author_agent_id=self._author_agent_id,
                body=body,
                tags=parsed.tags,
                related_task_ids=parsed.related_task_ids,
                slug=parsed.slug,
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                DOC_WRITE_FAILED,
                project_id=self._project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Write failed: invalid argument shape "
                    f"({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        except Exception as exc:
            logger.error(
                DOC_WRITE_FAILED,
                project_id=self._project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Write failed: {type(exc).__name__} "
                    f"({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        logger.info(
            DOC_WRITTEN,
            project_id=self._project_id,
            slug=metadata.slug,
            doc_type=metadata.doc_type.value,
        )
        return ToolExecutionResult(
            content=(
                f"Wrote living doc {metadata.doc_type.value}/{metadata.slug}"
                f" at {metadata.head_commit_sha[:12]}"
            ),
            metadata={
                "slug": metadata.slug,
                "doc_type": metadata.doc_type.value,
                "head_commit_sha": metadata.head_commit_sha,
            },
        )


def _materialise_body(
    blocks: tuple[WriteLivingDocBlockArg, ...],
) -> tuple[DocBlock, ...]:
    """Convert agent-facing block args to typed :data:`DocBlock` instances."""
    materialised: list[DocBlock] = [_one_block(block) for block in blocks]
    return tuple(materialised)


def _one_block(  # noqa: PLR0911, PLR0912, C901 -- one branch per block kind
    block: WriteLivingDocBlockArg,
) -> DocBlock:
    kind = block.block_kind
    if kind == "heading":
        if block.level is None or block.text is None:
            msg = "heading blocks require level and text"
            raise ValueError(msg)
        return HeadingBlock(level=block.level, text=block.text)
    if kind == "prose":
        if block.text is None:
            msg = "prose blocks require text"
            raise ValueError(msg)
        return ProseBlock(text=block.text)
    if kind == "bullet_list":
        if not block.items:
            msg = "bullet_list blocks require items"
            raise ValueError(msg)
        return BulletListBlock(items=block.items)
    if kind == "code":
        if block.code is None:
            msg = "code blocks require code"
            raise ValueError(msg)
        return CodeBlock(language=block.language, code=block.code)
    if kind == "decision":
        if block.decision is None or block.rationale is None:
            msg = "decision blocks require decision and rationale"
            raise ValueError(msg)
        return DecisionBlock(decision=block.decision, rationale=block.rationale)
    if kind == "metric":
        if block.name is None or block.value is None:
            msg = "metric blocks require name and value"
            raise ValueError(msg)
        return MetricBlock(name=block.name, value=block.value, unit=block.unit)
    if kind == "link":
        if block.label is None or block.url is None:
            msg = "link blocks require label and url"
            raise ValueError(msg)
        return LinkBlock(label=block.label, url=block.url)
    msg = f"unknown block_kind {kind!r}"
    raise ValueError(msg)


__all__ = ["WriteLivingDocTool"]
