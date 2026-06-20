"""Evolution-outcome read endpoints for the self-improvement meta-loop.

Serves the durable per-agent evolution-outcome log (summary, paginated
outcomes, per-axis stats) recorded by the engine evolution loop. Reads go
through ``MetaStateSlice.evolution_read_service``; when persistence is
absent the service is unwired and every endpoint degrades to an empty
result rather than 503-ing.
"""

from datetime import timedelta
from typing import Final

from litestar import Controller, get
from litestar.datastructures import State

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.core.pagination import collect_all
from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.meta.signal_models import OrgEvolutionSummary
from synthorg.meta.state import MetaStateSlice

_DEFAULT_PAGE_SIZE: Final[int] = 50
_DEFAULT_EVOLUTION_WINDOW_DAYS: Final[int] = 30


def _outcome_to_dict(record: EvolutionOutcomeRecord) -> dict[str, object]:
    """Serialise an evolution outcome record for the read endpoints.

    Returns:
        A JSON-serialisable outcome dict.
    """
    return {
        "agent_id": str(record.agent_id),
        "axis": str(record.axis),
        "applied": record.applied,
        "proposed_at": record.proposed_at.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
    }


def _evolution_summary_to_dict(summary: OrgEvolutionSummary) -> dict[str, object]:
    """Serialise an org evolution summary for the summary endpoint.

    Returns:
        A JSON-serialisable summary dict.
    """
    return {
        "total_proposals": summary.total_proposals,
        "approval_rate": summary.approval_rate,
        "most_adapted_axis": (
            str(summary.most_adapted_axis)
            if summary.most_adapted_axis is not None
            else None
        ),
        "recent_outcomes": [
            {
                "agent_id": str(o.agent_id),
                "axis": str(o.axis),
                "applied": o.applied,
                "proposed_at": o.proposed_at.isoformat(),
            }
            for o in summary.recent_outcomes
        ],
    }


class MetaEvolutionController(Controller):
    """Read endpoints over the durable evolution-outcome log."""

    path = "/meta/evolution"
    tags = ["meta-evolution"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @get("/summary")
    async def get_evolution_summary(
        self,
        state: State,
        window_days: int = _DEFAULT_EVOLUTION_WINDOW_DAYS,
    ) -> ApiResponse[dict[str, object]]:
        """Summarise evolution outcomes over a recent window.

        Args:
            state: Application state (durable evolution read service).
            window_days: Look-back window in days (clamped to >= 1).

        Returns:
            The rolled-up org evolution summary; an empty summary when
            the durable store is unavailable (no persistence).
        """
        service = state.app_state.slice(MetaStateSlice).evolution_read_service
        if service is None:
            return ApiResponse[dict[str, object]](
                data=_evolution_summary_to_dict(OrgEvolutionSummary())
            )
        now = state.app_state.clock.now()
        since = now - timedelta(days=max(1, window_days))
        summary = await service.summary(since=since, until=now)
        return ApiResponse[dict[str, object]](data=_evolution_summary_to_dict(summary))

    @get("/outcomes")
    async def list_evolution_outcomes(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
        agent_id: str | None = None,
        axis: str | None = None,
    ) -> PaginatedResponse[dict[str, object]]:
        """List recorded evolution outcomes, newest-first, paginated.

        Args:
            state: Application state (durable evolution read service).
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.
            agent_id: Optional agent-id filter.
            axis: Optional adaptation-axis filter.

        Returns:
            Paginated outcome summaries; an empty page when the durable
            store is unavailable.
        """
        service = state.app_state.slice(MetaStateSlice).evolution_read_service
        outcomes: tuple[EvolutionOutcomeRecord, ...] = ()
        if service is not None:
            bound = service
            outcomes = await collect_all(
                lambda limit, offset: bound.list_outcomes(
                    limit=limit,
                    offset=offset,
                    agent_id=NotBlankStr(agent_id) if agent_id else None,
                    axis=NotBlankStr(axis) if axis else None,
                )
            )
        summaries = tuple(_outcome_to_dict(o) for o in outcomes)
        page, meta = paginate_cursor(
            summaries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[dict[str, object]](data=page, pagination=meta)

    @get("/axes/stats")
    async def get_evolution_axes_stats(
        self,
        state: State,
        window_days: int = _DEFAULT_EVOLUTION_WINDOW_DAYS,
    ) -> ApiResponse[dict[str, object]]:
        """Per-axis outcome counts over a recent window.

        Args:
            state: Application state (durable evolution read service).
            window_days: Look-back window in days (clamped to >= 1).

        Returns:
            ``{axes: [{axis, count}, ...]}`` highest count first; empty
            when the durable store is unavailable.
        """
        service = state.app_state.slice(MetaStateSlice).evolution_read_service
        axes: list[dict[str, object]] = []
        if service is not None:
            now = state.app_state.clock.now()
            since = now - timedelta(days=max(1, window_days))
            stats = await service.axis_stats(since=since, until=now)
            axes = [{"axis": str(axis), "count": count} for axis, count in stats]
        return ApiResponse[dict[str, object]](data={"axes": axes})


__all__ = ["MetaEvolutionController"]
