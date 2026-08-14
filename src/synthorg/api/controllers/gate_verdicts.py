# module-kind: controller
"""Read surface for the two completion-gate verdict archives.

Both gates are judged by roster agents, so "how good are this agent's
verdicts, and on which model" is a question about an ordinary member of the
org. It is answerable only once the archives are readable, which is what
these two controllers are for.

The two archives are twins (one row per gate run, the same filters, the same
newest-first order), so they share a module and a pagination helper while
staying two controllers: each is registered by the feature that owns its gate,
so an org running without the red-team gate does not advertise its archive.

Paging is keyset, on ``(recorded_at, report_id)``: a gate writes to these
tables while an operator reads them, and an offset would shift every later
page by however many verdicts landed in between, showing some twice and
skipping others.

Both read at ``require_read_access``, so an ``observer`` sees the red-team
findings too, and that is deliberate. A finding describes a weakness in a
deliverable the same observer can already read in full, alongside the audit
log, which sits at the same tier and records every security-relevant action
in the org. Withholding the finding while serving the artefact it is about
would hide the judgement, never the weakness. Every human role here belongs
to the operator's own organisation; the boundary that matters for an
exploitable weakness is the one around the deployment, not the one between
its staff.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Final

from litestar import Controller, Response, get
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.api.cursor import (
    CursorSecret,
    InvalidCursorError,
    decode_keyset_cursor,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_keyset_meta,
)
from synthorg.api.rate_limits.policies import per_op_rate_limit_from_policy
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
_CURSOR_SEPARATOR: Final[str] = "|"

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

    A tally over one page would report a window as a total, so the split is
    counted at the storage layer in one grouped read. That is the whole point
    of the panel: a reviewer who approves everything and one who rejects half
    are only distinguishable across their full history.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    by_verdict: Mapping[str, int] = Field(
        description="Count per verdict kind, keyed by the verdict value"
    )

    @computed_field
    @property
    def total(self) -> int:
        """Verdicts matching the filter.

        Derived rather than stored: it is the sum of the split by definition,
        and a stored copy is a second answer that can disagree with the first.

        Returns:
            The total across every verdict kind.
        """
        return sum(self.by_verdict.values())


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


def _decode_position(
    cursor: str | None, *, secret: CursorSecret
) -> tuple[datetime | None, int | None]:
    """Split a keyset cursor back into the row position it names.

    Args:
        cursor: The opaque cursor from the previous page, or ``None``.
        secret: The HMAC secret the cursor was signed with.

    Returns:
        The ``(recorded_at, report_id)`` pair, or ``(None, None)`` for a
        first page.

    Raises:
        InvalidCursorError: If the token is unsigned, malformed, or does not
            carry both halves of a position.
    """
    if cursor is None:
        return None, None
    raw = decode_keyset_cursor(cursor, secret=secret)
    stamp, _, key = raw.partition(_CURSOR_SEPARATOR)
    if not key:
        msg = "cursor does not name a row position"
        raise InvalidCursorError(msg)
    try:
        return datetime.fromisoformat(stamp).astimezone(UTC), int(key)
    except ValueError as exc:
        msg = "cursor does not name a row position"
        raise InvalidCursorError(msg) from exc


def _encode_position(record: CompletionOracleReportRecord | RedTeamReportRecord) -> str:
    """Render one row's sort position as a cursor key.

    Args:
        record: The last row on the page just returned.

    Returns:
        The ``"<recorded_at>|<report_id>"`` key.
    """
    return f"{record.recorded_at.isoformat()}{_CURSOR_SEPARATOR}{record.report_id}"


class CompletionOracleReportController(Controller):
    """Peer-review verdicts the completion oracle archived."""

    path = "/completion-oracle/reports"
    tags = ("completion_oracle",)

    @get(
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("gate_verdicts.list", key="user"),
        ]
    )
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
        after_recorded_at, after_report_id = _decode_position(cursor, secret=secret)
        repo = persistence_of(state.app_state).completion_oracle_reports
        filter_spec = CompletionOracleReportFilterSpec(
            execution_id=_optional(execution_id),
            task_id=_optional(task_id),
            verdict=verdict,
            reviewer_agent_id=_optional(reviewer_agent_id),
            after_recorded_at=after_recorded_at,
            after_report_id=after_report_id,
        )
        # One row past the page, so "is there more" is observed rather than
        # inferred from a total taken at a different instant.
        page = await repo.query(filter_spec, limit=limit + 1)
        records = page[:limit]
        logger.debug(
            COMPLETION_ORACLE_REPORTS_LISTED,
            count=len(records),
            reviewer_agent_id=reviewer_agent_id,
        )
        meta = encode_keyset_meta(
            next_after_key=_encode_position(records[-1]) if records else None,
            has_more=len(page) > limit,
            limit=limit,
            secret=secret,
        )
        return Response(
            content=PaginatedResponse[CompletionOracleReportRecord](
                data=records,
                pagination=meta,
            ),
        )

    @get(
        "/summary",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("gate_verdicts.summary", key="user"),
        ],
    )
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
        held = await repo.count_by_verdict(
            CompletionOracleReportFilterSpec(
                task_id=_optional(task_id),
                reviewer_agent_id=_optional(reviewer_agent_id),
            )
        )
        # Every kind appears, zero included: the panel compares reviewers, and
        # a missing key would render as "no data" where the honest answer is
        # "this reviewer has never rejected anything".
        by_verdict = {
            kind.value: held.get(kind.value, 0) for kind in CompletionOracleVerdict
        }
        return Response(
            content=ApiResponse[GateVerdictSummary](
                data=GateVerdictSummary(by_verdict=by_verdict)
            ),
        )


class RedTeamReportController(Controller):
    """Adversarial verdicts the red-team gate archived."""

    path = "/red-team/reports"
    tags = ("red_team",)

    @get(
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("gate_verdicts.list", key="user"),
        ]
    )
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
        after_recorded_at, after_report_id = _decode_position(cursor, secret=secret)
        repo = persistence_of(state.app_state).red_team_reports
        filter_spec = RedTeamReportFilterSpec(
            execution_id=_optional(execution_id),
            task_id=_optional(task_id),
            verdict=verdict,
            red_team_agent_id=_optional(red_team_agent_id),
            after_recorded_at=after_recorded_at,
            after_report_id=after_report_id,
        )
        page = await repo.query(filter_spec, limit=limit + 1)
        records = page[:limit]
        logger.debug(
            RED_TEAM_REPORTS_LISTED,
            count=len(records),
            red_team_agent_id=red_team_agent_id,
        )
        meta = encode_keyset_meta(
            next_after_key=_encode_position(records[-1]) if records else None,
            has_more=len(page) > limit,
            limit=limit,
            secret=secret,
        )
        return Response(
            content=PaginatedResponse[RedTeamReportRecord](
                data=records,
                pagination=meta,
            ),
        )

    @get(
        "/summary",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("gate_verdicts.summary", key="user"),
        ],
    )
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
        held = await repo.count_by_verdict(
            RedTeamReportFilterSpec(
                task_id=_optional(task_id),
                red_team_agent_id=_optional(red_team_agent_id),
            )
        )
        by_verdict = {kind.value: held.get(kind.value, 0) for kind in RedTeamVerdict}
        return Response(
            content=ApiResponse[GateVerdictSummary](
                data=GateVerdictSummary(by_verdict=by_verdict)
            ),
        )


__all__ = [
    "CompletionOracleReportController",
    "GateVerdictSummary",
    "RedTeamReportController",
]
