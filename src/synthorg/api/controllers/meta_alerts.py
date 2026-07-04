"""Proactive-alert read endpoint for the self-improvement meta-loop.

Serves the durable org-alert log recorded by the org-inflection
monitor. Reads go through ``MetaStateSlice.alert_repo`` directly (no
read-service layer -- there is no rollup/GROUP-BY surface, unlike
evolution outcomes); when persistence is absent the repo is unwired
and the endpoint degrades to an empty result rather than 503-ing.
"""

from typing import Annotated, Final, Literal

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.meta.chief_of_staff.models import Alert
from synthorg.meta.models import RuleSeverity
from synthorg.meta.state import alert_repo_of
from synthorg.persistence.alert_protocol import AlertFilterSpec

_DEFAULT_PAGE_SIZE: Final[int] = 50

SeverityFilter = Annotated[
    RuleSeverity | None,
    QueryParameter(required=False, description="Optional severity filter."),
]
AlertTypeFilter = Annotated[
    Literal["inflection", "threshold", "trend"] | None,
    QueryParameter(required=False, description="Optional alert-type filter."),
]


def _alert_to_dict(alert: Alert) -> dict[str, object]:
    """Serialise an alert for the read endpoint.

    Returns:
        A JSON-serialisable alert dict.
    """
    return {
        "id": str(alert.id),
        "severity": alert.severity.value,
        "alert_type": alert.alert_type,
        "description": str(alert.description),
        "affected_domains": list(alert.affected_domains),
        "signal_context": dict(alert.signal_context),
        "recommended_action": (
            str(alert.recommended_action)
            if alert.recommended_action is not None
            else None
        ),
        "emitted_at": alert.emitted_at.isoformat(),
    }


class MetaAlertsController(Controller):
    """Read endpoint over the durable org-alert log."""

    path = "/meta/alerts"
    tags = ["meta-alerts"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @get("/")
    async def list_alerts(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
        severity: SeverityFilter = None,
        alert_type: AlertTypeFilter = None,
    ) -> PaginatedResponse[dict[str, object]]:
        """List recorded org alerts, newest-first, paginated.

        Args:
            state: Application state (durable alert repository).
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.
            severity: Optional severity filter.
            alert_type: Optional alert-type filter.

        Returns:
            Paginated alert summaries; an empty page when the durable
            repository is unavailable.
        """
        app_state = state.app_state
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        repo = alert_repo_of(app_state)
        alerts: tuple[Alert, ...] = ()
        if repo is not None:
            filter_spec = AlertFilterSpec(severity=severity, alert_type=alert_type)
            alerts = await repo.query(filter_spec, limit=limit + 1, offset=offset)
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(alerts),
            limit=limit,
            secret=secret,
        )
        page = tuple(_alert_to_dict(a) for a in alerts[:limit])
        return PaginatedResponse[dict[str, object]](data=page, pagination=meta)


__all__ = ["MetaAlertsController"]
