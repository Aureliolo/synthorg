"""Backend-agnostic row <-> model marshalling for cost forecasts.

The SQLite and Postgres cost-forecast repositories deserialise the same
``cost_forecasts`` columns into the same :class:`Forecast` model, build
the same filter predicates, and apply the same transition-key
validation. The row objects differ (``aiosqlite.Row`` vs psycopg
``dict_row``) but both support string-key indexing, so this module's
:class:`RowLike` marshaller serves both backends; the timestamp coercer
normalises ``TEXT`` / ``TIMESTAMPTZ`` alike and the ``forecast_id``
branch tolerates both a native :class:`~uuid.UUID` (Postgres) and a
string (SQLite).
"""

import json
from collections.abc import Callable
from typing import LiteralString
from uuid import UUID

from pydantic import JsonValue

from synthorg.budget.forecast_models import Forecast, ForecastDecision, HaltContext
from synthorg.core.persistence_errors import MalformedRowError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.cost_forecast import (
    PERSISTENCE_COST_FORECAST_FAILED,
)
from synthorg.persistence._shared.datetime_marshaller import (
    coerce_row_timestamp,
    format_iso_utc,
)
from synthorg.persistence._shared.rows import RowLike
from synthorg.persistence.cost_forecast_protocol import CostForecastFilterSpec

logger = get_logger(__name__)

COST_FORECAST_COLUMNS: LiteralString = (
    "forecast_id, brief_hash, estimated_cost, lower_bound, upper_bound, "
    "currency, decision, decided_at, decided_by, ceiling_amount, "
    "halt_accumulated_cost, halt_ceiling_amount, halt_currency, halted_at, "
    "gated_work_item, created_at, updated_at"
)

#: Adapts the gated work item to the backend's JSON binding (``json.dumps``
#: for SQLite's TEXT column, ``Jsonb`` for Postgres's JSONB one).
type JsonBinder = Callable[[dict[str, JsonValue]], object]

_ALLOWED_TRANSITION_KEYS = frozenset({"decided_by", "decided_at", "ceiling_amount"})


def row_to_forecast(row: RowLike) -> Forecast:
    """Convert a database row into a :class:`Forecast`.

    Returns:
        Result of type ``Forecast``.

    Raises:
        MalformedRowError: If the row contains corrupt or unparseable data.
    """
    try:
        decided_at_raw = row["decided_at"]
        halted_at_raw = row["halted_at"]
        forecast_raw = row["forecast_id"]
        halt_context = (
            HaltContext(
                accumulated_cost=float(str(row["halt_accumulated_cost"])),
                ceiling_amount=float(str(row["halt_ceiling_amount"])),
                currency=str(row["halt_currency"]),
                halted_at=coerce_row_timestamp(halted_at_raw),
            )
            if halted_at_raw is not None
            else None
        )
        return Forecast(
            forecast_id=(
                forecast_raw
                if isinstance(forecast_raw, UUID)
                else UUID(str(forecast_raw))
            ),
            brief_hash=str(row["brief_hash"]),
            estimated_cost=float(str(row["estimated_cost"])),
            lower_bound=float(str(row["lower_bound"])),
            upper_bound=float(str(row["upper_bound"])),
            currency=str(row["currency"]),
            decision=ForecastDecision(str(row["decision"])),
            decided_at=(
                coerce_row_timestamp(decided_at_raw)
                if decided_at_raw is not None
                else None
            ),
            decided_by=(
                str(row["decided_by"]) if row["decided_by"] is not None else None
            ),
            ceiling_amount=(
                float(str(row["ceiling_amount"]))
                if row["ceiling_amount"] is not None
                else None
            ),
            halt_context=halt_context,
            gated_work_item=_decode_gated_work_item(row["gated_work_item"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = (
            f"Failed to parse cost forecast row: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            PERSISTENCE_COST_FORECAST_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MalformedRowError(msg) from exc


def _decode_gated_work_item(raw: object) -> dict[str, JsonValue] | None:
    """Decode the stored work item from either backend's JSON column.

    Returns:
        The decoded mapping, or ``None`` when the column is empty.

    Raises:
        TypeError: When the column holds something that is not a JSON
            object, which would otherwise reach the re-dispatch parser as
            a silently wrong shape.
    """
    if raw is None:
        return None
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(decoded, dict):
        msg = "gated_work_item must be a JSON object"
        raise TypeError(msg)
    return decoded


def forecast_save_params(
    entity: Forecast, *, bind_json: JsonBinder
) -> tuple[object, ...]:
    """Flatten a forecast into the positional upsert params.

    Args:
        entity: The forecast to flatten.
        bind_json: The backend's binding for the ``gated_work_item``
            JSON column.

    Returns:
        The matching collection.
    """
    halt = entity.halt_context
    return (
        str(entity.forecast_id),
        entity.brief_hash,
        float(entity.estimated_cost),
        float(entity.lower_bound),
        float(entity.upper_bound),
        entity.currency,
        entity.decision.value,
        (format_iso_utc(entity.decided_at) if entity.decided_at is not None else None),
        entity.decided_by,
        (float(entity.ceiling_amount) if entity.ceiling_amount is not None else None),
        (float(halt.accumulated_cost) if halt is not None else None),
        (float(halt.ceiling_amount) if halt is not None else None),
        (halt.currency if halt is not None else None),
        (format_iso_utc(halt.halted_at) if halt is not None else None),
        (
            bind_json(entity.gated_work_item)
            if entity.gated_work_item is not None
            else None
        ),
        format_iso_utc(entity.created_at),
        format_iso_utc(entity.updated_at),
    )


def build_cost_forecast_where(
    filter_spec: CostForecastFilterSpec, *, placeholder: LiteralString
) -> tuple[LiteralString, list[object]]:
    """Build the WHERE clause + bound params from a filter spec.

    Args:
        filter_spec: The forecast filter predicates.
        placeholder: The backend's bound-parameter token (``?`` / ``%s``).

    Returns:
        ``(where_clause, params)``: SQL fragment + positional params.
    """
    clauses: list[LiteralString] = []
    params: list[object] = []
    if filter_spec.brief_hash is not None:
        clauses.append(f"brief_hash = {placeholder}")
        params.append(filter_spec.brief_hash)
    if filter_spec.decision is not None:
        clauses.append(f"decision = {placeholder}")
        params.append(filter_spec.decision.value)
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


def validate_cost_forecast_update_keys(
    operation: str,
    forecast_id: UUID,
    updates: dict[str, object],
    *,
    to_state: ForecastDecision,
) -> None:
    """Reject unknown update keys; validate ``superseded`` semantics.

    ``superseded`` is a system transition (the operator edited the
    brief); ``decided_by`` is forbidden there.

    Raises:
        QueryError: If unknown update keys or invalid transition inputs
            are supplied.
    """
    unknown = sorted(set(updates) - _ALLOWED_TRANSITION_KEYS)
    if unknown:
        msg = f"transition_if rejects unknown update keys: {unknown!r}"
        logger.warning(
            PERSISTENCE_COST_FORECAST_FAILED,
            operation=operation,
            forecast_id=str(forecast_id),
            error=msg,
        )
        raise QueryError(msg)
    if to_state is ForecastDecision.SUPERSEDED and "decided_by" in updates:
        msg = "transition to 'superseded' must not carry decided_by"
        logger.warning(
            PERSISTENCE_COST_FORECAST_FAILED,
            operation=operation,
            forecast_id=str(forecast_id),
            error=msg,
        )
        raise QueryError(msg)


def _clear_halt_sql(placeholder: LiteralString) -> LiteralString:
    """Assemble the halt-guarded ceiling-raise UPDATE for one placeholder.

    Clears the four ``halt_*`` columns and raises the ceiling only while
    the row is still halted (``halted_at IS NOT NULL``). A concurrent
    ceiling-raise that already cleared the halt leaves the row unmatched,
    so the second writer affects zero rows and the caller surfaces a
    conflict rather than a no-op masquerading as success (Slot 39 CAS).

    Returns:
        The full conditional ``UPDATE`` statement.
    """
    return (
        f"UPDATE cost_forecasts SET ceiling_amount = {placeholder}, "  # noqa: S608 -- constants only
        "halt_accumulated_cost = NULL, halt_ceiling_amount = NULL, "
        "halt_currency = NULL, halted_at = NULL, "
        f"updated_at = {placeholder} "
        f"WHERE forecast_id = {placeholder} AND halted_at IS NOT NULL"
    )


FORECAST_CLEAR_HALT_SQL_QMARK: LiteralString = _clear_halt_sql("?")
"""Halt-guarded ceiling-raise UPDATE (SQLite ``?`` token)."""

FORECAST_CLEAR_HALT_SQL_PCT: LiteralString = _clear_halt_sql("%s")
"""Halt-guarded ceiling-raise UPDATE (Postgres ``%s`` token)."""


def _claim_sql(placeholder: LiteralString) -> LiteralString:
    """Assemble the unclaimed-guarded work-item claim for one placeholder.

    Attaches the work item and re-keys the digest to the claiming
    submission only while the row is still free (``gated_work_item IS
    NULL``). A standalone estimate carries an approved ceiling, so two
    submissions reading it free and both writing would spend one approval
    twice; the condition in the statement leaves the loser unmatched, and
    it is told so rather than sharing the winner's approval.

    Returns:
        The full conditional ``UPDATE`` statement.
    """
    return (
        f"UPDATE cost_forecasts SET gated_work_item = {placeholder}, "  # noqa: S608 -- constants only
        f"brief_hash = {placeholder}, updated_at = {placeholder} "
        f"WHERE forecast_id = {placeholder} AND gated_work_item IS NULL"
    )


FORECAST_CLAIM_SQL_QMARK: LiteralString = _claim_sql("?")
"""Unclaimed-guarded work-item claim (SQLite ``?`` token)."""

FORECAST_CLAIM_SQL_PCT: LiteralString = _claim_sql("%s")
"""Unclaimed-guarded work-item claim (Postgres ``%s`` token)."""


__all__ = [
    "COST_FORECAST_COLUMNS",
    "FORECAST_CLAIM_SQL_PCT",
    "FORECAST_CLAIM_SQL_QMARK",
    "FORECAST_CLEAR_HALT_SQL_PCT",
    "FORECAST_CLEAR_HALT_SQL_QMARK",
    "JsonBinder",
    "build_cost_forecast_where",
    "forecast_save_params",
    "row_to_forecast",
    "validate_cost_forecast_update_keys",
]
