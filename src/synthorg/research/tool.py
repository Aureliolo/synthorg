"""``ResearchTool`` -- agent-facing research capability.

Given a research brief, runs the full pipeline (plan -> multi-source
retrieval -> credibility triage -> dedup -> cited synthesis) and returns a
citation-backed report. The brief / run identifiers are derived
deterministically from the request so an identical request reproduces the
same run id (idempotent re-run, replay-friendly).
"""

from typing import TYPE_CHECKING, Any, ClassVar, Final

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
)

from synthorg.api.boundary import parse_typed
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import ActionType, ToolCategory
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.research import (
    RESEARCH_RUN_COMPLETED,
    RESEARCH_RUN_FAILED,
)
from synthorg.research.constants import (
    RESEARCH_DEFAULT_MAX_SUBQUERIES,
    RESEARCH_DEFAULT_MIN_CREDIBILITY,
)
from synthorg.research.errors import ResearchError
from synthorg.research.models import QuestionText, ResearchBrief, ResearchReport
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from synthorg.research.service import ResearchService

logger = get_logger(__name__)

_BRIEF_ARGS_BOUNDARY = "research.brief_args"
_RUN_ID_HASH_LEN: Final[int] = 16
_TITLE_FALLBACK_LEN: Final[int] = 120


class ResearchBriefArgs(BaseModel):
    """Args for the ``research`` tool / ``research:run`` MCP tool."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    question: QuestionText = Field(description="The research question to answer")
    title: NotBlankStr | None = Field(
        default=None,
        description="Optional report title; defaults to a slice of the question",
    )
    include_knowledge: bool = Field(default=True)
    include_web: bool = Field(default=True)
    include_academic: bool = Field(default=False)
    include_code: bool = Field(default=False)
    min_credibility: float = Field(
        default=RESEARCH_DEFAULT_MIN_CREDIBILITY, ge=0.0, le=1.0
    )
    max_subqueries: int = Field(default=RESEARCH_DEFAULT_MAX_SUBQUERIES, ge=1)


def derive_research_ids(
    args: ResearchBriefArgs,
    *,
    project_id: NotBlankStr | None,
) -> tuple[NotBlankStr, NotBlankStr]:
    """Derive deterministic ``(brief_id, run_id)`` from a request.

    Identical requests reproduce the same identifiers, so a re-run upserts
    the same row (idempotent, replay-friendly). Shared by the agent tool
    and the MCP handler so both surfaces address the same run.
    """
    key = "|".join(
        [
            args.question,
            project_id or "",
            str(args.include_knowledge),
            str(args.include_web),
            str(args.include_academic),
            str(args.include_code),
            f"{args.min_credibility:.4f}",
            str(args.max_subqueries),
        ]
    )
    digest = compute_text_hash(key)[:_RUN_ID_HASH_LEN]
    return NotBlankStr(f"brief-{digest}"), NotBlankStr(f"run-{digest}")


def build_research_brief(
    args: ResearchBriefArgs,
    *,
    brief_id: NotBlankStr,
    project_id: NotBlankStr | None,
    created_at: AwareDatetime,
) -> ResearchBrief:
    """Build a :class:`ResearchBrief` from validated request args."""
    title = (
        args.title
        if args.title is not None
        else NotBlankStr(args.question[:_TITLE_FALLBACK_LEN])
    )
    return ResearchBrief(
        brief_id=brief_id,
        project_id=project_id,
        title=title,
        question=args.question,
        include_knowledge=args.include_knowledge,
        include_web=args.include_web,
        include_academic=args.include_academic,
        include_code=args.include_code,
        min_credibility=args.min_credibility,
        max_subqueries=args.max_subqueries,
        created_at=created_at,
    )


def _render_report(report: ResearchReport) -> str:
    """Render a report as compact, citation-annotated text for the agent."""
    lines = [f"# {report.title}", "", report.summary, "", "## Claims"]
    for claim in report.claims:
        refs = ", ".join(citation.ref_id for citation in claim.citations)
        lines.append(f"- ({claim.claim_type.value}) {claim.text} [sources: {refs}]")
    return "\n".join(lines)


class ResearchTool(BaseTool):
    """Agent tool that runs a research brief and returns a cited report."""

    args_model: ClassVar[type[BaseModel] | None] = ResearchBriefArgs

    def __init__(
        self,
        *,
        service: ResearchService,
        project_id: NotBlankStr | None,
        created_by: NotBlankStr,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            name="research",
            description=(
                "Conduct a research task: given a question, plan queries, "
                "consult internal knowledge plus web / academic / code "
                "sources, triage source credibility, and synthesise a "
                "citation-backed report whose claims resolve to retrievable "
                "sources."
            ),
            parameters_schema=ResearchBriefArgs.model_json_schema(),
            category=ToolCategory.EXTERNAL_DATA,
            action_type=ActionType.RESEARCH_RUN.value,
        )
        self._service = service
        self._project_id = project_id
        self._created_by = created_by
        self._clock = clock if clock is not None else SystemClock()

    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Run the research pipeline and return the cited report."""
        args = parse_typed(_BRIEF_ARGS_BOUNDARY, arguments, ResearchBriefArgs)
        brief_id, run_id = derive_research_ids(args, project_id=self._project_id)
        brief = build_research_brief(
            args,
            brief_id=brief_id,
            project_id=self._project_id,
            created_at=self._clock.now(),
        )
        try:
            run = await self._service.run(
                brief, run_id=run_id, created_by=self._created_by
            )
        except ResearchError as exc:
            logger.warning(
                RESEARCH_RUN_FAILED,
                run_id=run_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content="Research failed. Please retry or narrow the brief.",
                is_error=True,
            )
        report = run.report
        if report is None:  # defensive: run() guarantees a report on success
            return ToolExecutionResult(
                content="Research produced no report.", is_error=True
            )
        logger.info(RESEARCH_RUN_COMPLETED, run_id=run_id, claims=len(report.claims))
        return ToolExecutionResult(
            content=_render_report(report),
            metadata={
                "run_id": run_id,
                "claim_count": len(report.claims),
                "sources_retained": report.sources_retained,
            },
        )
