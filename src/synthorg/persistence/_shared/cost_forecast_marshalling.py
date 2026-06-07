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

from typing import LiteralString
from uuid import UUID

from synthorg.budget.forecast_models import Forecast, ForecastDecision, HaltContext
from synthorg.core.persistence_errors import QueryError
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
    "created_at, updated_at"
)

_ALLOWED_TRANSITION_KEYS = frozenset({"decided_by", "decided_at", "ceiling_amount"})


def row_to_forecast(row: RowLike) -> Forecast:
    """Convert a database row into a :class:`Forecast`.

    Returns:
        Result of type ``Forecast``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
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
        raise QueryError(msg) from exc


def forecast_save_params(entity: Forecast) -> tuple[object, ...]:
    """Flatten a forecast into the positional upsert params.

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


__all__ = [
    "COST_FORECAST_COLUMNS",
    "build_cost_forecast_where",
    "forecast_save_params",
    "row_to_forecast",
    "validate_cost_forecast_update_keys",
]
