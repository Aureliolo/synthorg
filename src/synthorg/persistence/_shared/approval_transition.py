"""Shared decision-update handling for ``ApprovalRepository.transition_if``.

Both backend repositories accept the same status-correlated decision
triple (``decided_at`` / ``decided_by`` / ``decision_reason``) on an
``approved`` / ``rejected`` compare-and-set, written atomically with the
status flip. The key validation and value extraction live here so the
SQLite and Postgres implementations cannot drift; each backend supplies
its own ``decided_at`` formatter (ISO-text vs aware-UTC) and placeholder
style and binds the returned triple into a fixed ``COALESCE`` ``UPDATE``.
"""

from collections.abc import Callable, Mapping
from datetime import datetime

from synthorg.core.persistence_errors import QueryError

ALLOWED_APPROVAL_TRANSITION_KEYS: frozenset[str] = frozenset(
    ("decided_at", "decided_by", "decision_reason")
)


def approval_decision_values(
    entity_id: object,
    updates: Mapping[str, object],
    *,
    format_decided_at: Callable[[datetime], object],
) -> tuple[object, object, object]:
    """Validate decision updates and extract the ordered decision triple.

    The returned tuple is bound positionally into a fixed ``COALESCE``
    ``UPDATE`` (``decided_at``, ``decided_by``, ``decision_reason``); a
    ``None`` element leaves the existing column value untouched, so an
    update carrying no decision keys (e.g. a plain ``EXPIRED`` flip)
    changes only the status.

    Args:
        entity_id: The approval id (for error context only).
        updates: The ``**updates`` mapping passed to ``transition_if``.
        format_decided_at: Backend formatter applied to a ``datetime``
            ``decided_at`` value (ISO text for SQLite, aware-UTC for
            Postgres TIMESTAMPTZ). A non-``datetime`` value passes
            through unchanged so the DB layer rejects it.

    Returns:
        ``(decided_at, decided_by, decision_reason)`` with ``None`` for
        any field the caller did not supply.

    Raises:
        QueryError: When ``updates`` contains an unknown key.
    """
    extra = set(updates) - ALLOWED_APPROVAL_TRANSITION_KEYS
    if extra:
        msg = (
            f"transition_if got unknown update keys {sorted(extra)!r} "
            f"for approval {entity_id!r}"
        )
        raise QueryError(msg)

    decided_at_raw = updates.get("decided_at")
    decided_at: object = (
        format_decided_at(decided_at_raw)
        if isinstance(decided_at_raw, datetime)
        else decided_at_raw
    )
    return decided_at, updates.get("decided_by"), updates.get("decision_reason")


__all__ = ["ALLOWED_APPROVAL_TRANSITION_KEYS", "approval_decision_values"]
