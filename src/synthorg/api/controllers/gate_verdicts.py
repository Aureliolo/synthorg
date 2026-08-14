# module-kind: controller
"""Read surface for the two completion-gate verdict archives.

Both gates are now judged by roster agents rather than by a synthetic
singleton, so "how good are this agent's verdicts, and on which model" is a
question about an ordinary member of the org. It only becomes answerable once
the archives are readable, which is what these two controllers are for: the
rows have been written since the gates shipped and nothing has ever exposed
them.

The two archives are twins (one row per gate run, the same filters, the same
newest-first order), so they share a module and a pagination helper while
staying two controllers: each is registered by the feature that owns its gate,
so an org running without the red-team gate does not advertise its archive.
"""

import asyncio
from collections.abc import Mapping
from typing import Annotated, Final

from litestar import Controller, Response, get
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_repo_seek_meta,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.observability import get_logger
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_REPORTS_LISTED,
)
from synthorg.observability.events.red_team import RED_TEAM_REPORTS_LISTED
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportFilterSpec,
)
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.persistence.state import persistence_of
from synthorg.security.redteam.models import RedTeamReportRecord, RedTeamVerdict

logger = get_logger(__name__)

_DEFAULT_LIMIT: Final[int] = 20

ExecutionIdParam = Annotated[
    str | None,
    QueryParameter(description="Only verdicts reached on this execution"),
]
TaskIdParam = Annotated[
    str | None,
    QueryParameter(description="Only verdicts reached on this task"),
]
JudgeIdParam = Annotated[
    str | None,
    QueryParameter(description="Only verdicts reached by this agent"),
]
OracleVerdictParam = Annotated[
    CompletionOracleVerdict | None,
    QueryParameter(description="Only verdicts of this kind"),
]
RedTeamVerdictParam = Annotated[
    RedTeamVerdict | None,
    QueryParameter(description="Only verdicts of this kind"),
]


class GateVerdictSummary(BaseModel):
    """How a judge's verdicts split by kind.

    A tally over one page would report a window as a total, so each kind is
    counted at the storage layer. That is the whole point of the panel: a
    reviewer who approves everything and one who rejects half are only
    distinguishable across their full history.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total: int = Field(ge=0, description="Verdicts matching the filter")
    by_verdict: Mapping[str, int] = Field(
        description="Count per verdict kind, keyed by the verdict value"
    )


def _optional(value: str | None) -> NotBlankStr | None:
    """Narrow an optional query parameter to the filter spec's field type.

    A query parameter present but blank is the same request as one absent:
    the caller named no value, so it filters on nothing.

    Args:
        value: The raw query-parameter value.

    Returns:
        The trimmed value, or ``None`` when the caller supplied none.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return NotBlankStr(trimmed) if trimmed else None


class CompletionOracleReportController(Controller):
    """Peer-review verdicts the completion oracle archived."""

    path = "/completion-oracle/reports"
    tags = ("completion_oracle",)

    @get(guards=[require_read_access])
    async def list_oracle_reports(
        self,
        state: State,
        *,
        execution_id: ExecutionIdParam = None,
        task_id: TaskIdParam = None,
        verdict: OracleVerdictParam = None,
        reviewer_agent_id: JudgeIdParam = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> Response[PaginatedResponse[CompletionOracleReportRecord]]:
        """List archived peer-review verdicts, newest first.

        Returns:
            ``Response[PaginatedResponse[CompletionOracleReportRecord]]``.
        """
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        repo = persistence_of(state.app_state).completion_oracle_reports
        filter_spec = CompletionOracleReportFilterSpec(
            execution_id=_optional(execution_id),
            task_id=_optional(task_id),
            verdict=verdict,
            reviewer_agent_id=_optional(reviewer_agent_id),
        )
        records, total = await asyncio.gather(
            repo.query(filter_spec, limit=limit, offset=offset),
            repo.count(filter_spec),
        )
        logger.debug(
            COMPLETION_ORACLE_REPORTS_LISTED,
            count=len(records),
            reviewer_agent_id=reviewer_agent_id,
        )
        meta = encode_repo_seek_meta(
            offset=offset,
            page_len=len(records),
            total=total,
            limit=limit,
            secret=secret,
        )
        return Response(
            content=PaginatedResponse[CompletionOracleReportRecord](
                data=records,
                pagination=meta,
            ),
        )

    @get("/summary", guards=[require_read_access])
    async def summarise_oracle_reports(
        self,
        state: State,
        *,
        task_id: TaskIdParam = None,
        reviewer_agent_id: JudgeIdParam = None,
    ) -> Response[ApiResponse[GateVerdictSummary]]:
        """Count archived peer-review verdicts by kind.

        Returns:
            ``Response[ApiResponse[GateVerdictSummary]]``.
        """
        repo = persistence_of(state.app_state).completion_oracle_reports
        base = CompletionOracleReportFilterSpec(
            task_id=_optional(task_id),
            reviewer_agent_id=_optional(reviewer_agent_id),
        )
        kinds = tuple(CompletionOracleVerdict)
        counts = await asyncio.gather(
            *(repo.count(base.model_copy(update={"verdict": k})) for k in kinds)
        )
        by_verdict = {k.value: n for k, n in zip(kinds, counts, strict=True)}
        return Response(
            content=ApiResponse[GateVerdictSummary](
                data=GateVerdictSummary(
                    total=sum(counts),
                    by_verdict=by_verdict,
                )
            ),
        )


class RedTeamReportController(Controller):
    """Adversarial verdicts the red-team gate archived."""

    path = "/red-team/reports"
    tags = ("red_team",)

    @get(guards=[require_read_access])
    async def list_red_team_reports(
        self,
        state: State,
        *,
        execution_id: ExecutionIdParam = None,
        task_id: TaskIdParam = None,
        verdict: RedTeamVerdictParam = None,
        red_team_agent_id: JudgeIdParam = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> Response[PaginatedResponse[RedTeamReportRecord]]:
        """List archived adversarial verdicts, newest first.

        Returns:
            ``Response[PaginatedResponse[RedTeamReportRecord]]``.
        """
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        repo = persistence_of(state.app_state).red_team_reports
        filter_spec = RedTeamReportFilterSpec(
            execution_id=_optional(execution_id),
            task_id=_optional(task_id),
            verdict=verdict,
            red_team_agent_id=_optional(red_team_agent_id),
        )
        records, total = await asyncio.gather(
            repo.query(filter_spec, limit=limit, offset=offset),
            repo.count(filter_spec),
        )
        logger.debug(
            RED_TEAM_REPORTS_LISTED,
            count=len(records),
            red_team_agent_id=red_team_agent_id,
        )
        meta = encode_repo_seek_meta(
            offset=offset,
            page_len=len(records),
            total=total,
            limit=limit,
            secret=secret,
        )
        return Response(
            content=PaginatedResponse[RedTeamReportRecord](
                data=records,
                pagination=meta,
            ),
        )

    @get("/summary", guards=[require_read_access])
    async def summarise_red_team_reports(
        self,
        state: State,
        *,
        task_id: TaskIdParam = None,
        red_team_agent_id: JudgeIdParam = None,
    ) -> Response[ApiResponse[GateVerdictSummary]]:
        """Count archived adversarial verdicts by kind.

        Returns:
            ``Response[ApiResponse[GateVerdictSummary]]``.
        """
        repo = persistence_of(state.app_state).red_team_reports
        base = RedTeamReportFilterSpec(
            task_id=_optional(task_id),
            red_team_agent_id=_optional(red_team_agent_id),
        )
        kinds = tuple(RedTeamVerdict)
        counts = await asyncio.gather(
            *(repo.count(base.model_copy(update={"verdict": k})) for k in kinds)
        )
        by_verdict = {k.value: n for k, n in zip(kinds, counts, strict=True)}
        return Response(
            content=ApiResponse[GateVerdictSummary](
                data=GateVerdictSummary(
                    total=sum(counts),
                    by_verdict=by_verdict,
                )
            ),
        )


__all__ = [
    "CompletionOracleReportController",
    "GateVerdictSummary",
    "RedTeamReportController",
]
