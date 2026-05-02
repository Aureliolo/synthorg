"""Reports controller -- automated report generation and retrieval."""

from typing import TYPE_CHECKING

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    paginate_cursor,
)
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.budget.report_config import ReportPeriod
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_SERVICE_UNAVAILABLE

if TYPE_CHECKING:
    from synthorg.budget.automated_reports import AutomatedReportService
    from synthorg.budget.report_templates import ComprehensiveReport

logger = get_logger(__name__)

_SERVICE_UNAVAILABLE_MSG = "Automated reporting service not configured"


# ── Request/Response models ──────────────────────────────────────


class GenerateReportRequest(BaseModel):
    """Request body for on-demand report generation.

    Attributes:
        period: The report period (daily/weekly/monthly).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    period: ReportPeriod = Field(description="Report period")


class ReportResponse(BaseModel):
    """Serializable report response.

    Attributes:
        period: The report period.
        start: Period start (ISO 8601).
        end: Period end (ISO 8601).
        has_spending: Whether spending data is included.
        has_performance: Whether performance data is included.
        has_task_completion: Whether task completion data is included.
        has_risk_trends: Whether risk trends data is included.
        generated_at: Generation timestamp (ISO 8601).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    period: ReportPeriod
    start: AwareDatetime
    end: AwareDatetime
    has_spending: bool = False
    has_performance: bool = False
    has_task_completion: bool = False
    has_risk_trends: bool = False
    generated_at: AwareDatetime


def _to_report_response(
    report: ComprehensiveReport,
) -> ReportResponse:
    """Convert a comprehensive report to its API response DTO."""
    return ReportResponse(
        period=report.period,
        start=report.start,
        end=report.end,
        has_spending=report.spending is not None,
        has_performance=report.performance is not None,
        has_task_completion=report.task_completion is not None,
        has_risk_trends=report.risk_trends is not None,
        generated_at=report.generated_at,
    )


def _get_report_service(
    state: State,
) -> AutomatedReportService:
    """Resolve the report service from app state."""
    app_state: AppState = state.app_state
    service = app_state.report_service if app_state.has_report_service else None
    if service is None:
        logger.warning(
            API_SERVICE_UNAVAILABLE,
            service="report_service",
        )
        raise ServiceUnavailableError(_SERVICE_UNAVAILABLE_MSG)
    return service


# ── Controller ───────────────────────────────────────────────────


class ReportsController(Controller):
    """Automated report generation endpoints."""

    path = "/reports"
    guards = [require_read_access]  # noqa: RUF012

    @post(
        "/generate",
        summary="Generate an on-demand report",
        description=("Trigger on-demand report generation for a given period."),
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("reports.generate", key="user"),
        ],
    )
    async def generate_report(
        self,
        state: State,
        data: GenerateReportRequest,
    ) -> ApiResponse[ReportResponse]:
        """Generate a comprehensive report on demand."""
        service = _get_report_service(state)
        # Service owns lifecycle logging (STARTED + COMPLETED events).
        report = await service.generate_comprehensive_report(
            period=data.period,
        )
        return ApiResponse(data=_to_report_response(report))

    @get(
        "/periods",
        summary="List available report periods",
        description="Return the available report period options (paginated).",
    )
    async def list_periods(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> PaginatedResponse[str]:
        """List available report periods (cursor-paginated for shape parity)."""
        entries = tuple(p.value for p in ReportPeriod)
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[str](data=page, pagination=meta)
