"""Shared serialisation/deserialisation helpers for ``CustomRuleDefinition`` repos.

Both backends round-trip the same Pydantic model. Differences:
- SQLite stores altitudes as a TEXT JSON string and timestamps as
  ISO 8601 strings; row access is positional (``aiosqlite.Row``).
- Postgres stores altitudes as JSONB (returned as a Python list) and
  timestamps as TIMESTAMPTZ (returned as ``datetime``); row access is
  by column name (``psycopg.rows.dict_row``).

The helpers below take a unified ``dict[str, Any]`` row contract,
tolerate either encoding for ``target_altitudes``, and route both
backends through :func:`coerce_row_timestamp` so timestamp
normalisation is the conformance contract.

Conformance tests for ``CustomRuleRepository`` should target these
helpers directly so they exercise the canonical contract without
instantiating either backend.
"""

import json
from typing import Any

from pydantic import ValidationError

from synthorg.meta.models import ProposalAltitude
from synthorg.meta.rules.custom import CustomRuleDefinition
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_CUSTOM_RULE_FETCH_FAILED
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence.errors import MalformedRowError

logger = get_logger(__name__)

__all__ = (
    "row_to_custom_rule",
    "serialize_altitudes",
)


def serialize_altitudes(rule: CustomRuleDefinition) -> list[str]:
    """Return the rule's target altitudes as a JSON-ready list.

    Backends wrap this in ``json.dumps`` (SQLite TEXT) or
    ``psycopg.types.json.Jsonb`` (Postgres JSONB).

    Args:
        rule: The custom rule whose ``target_altitudes`` tuple should
            be serialised.

    Returns:
        ``list[str]`` of the rule's altitude enum ``.value`` strings,
        in the original tuple order.  Safe to pass to ``json.dumps``
        or wrap in a JSONB adapter.
    """
    return [a.value for a in rule.target_altitudes]


def row_to_custom_rule(row: dict[str, Any]) -> CustomRuleDefinition:
    """Deserialise a row mapping into a :class:`CustomRuleDefinition`.

    Tolerates both string-encoded ``target_altitudes`` (SQLite TEXT)
    and natively-decoded list (Postgres JSONB), and both ISO-string
    timestamps (SQLite) and ``datetime`` timestamps (Postgres).
    Timestamps are normalised to UTC before being stored on the model
    so backend round-trips compare equal in conformance tests.

    Scalar fields (``id``, ``name``, ``description``, ``metric_path``,
    ``comparator``, ``threshold``, ``severity``, ``enabled``) are
    passed through to the Pydantic constructor without coercion so
    the model's own validators can flag malformed rows -- a manual
    ``str(...)``/``bool(...)`` wrapper would silently mask a corrupt
    column (e.g. truthy non-bool, non-string identifier) and produce
    a usable model that no longer represents what the database held.

    Args:
        row: Mapping from column name to raw driver value. Required
            keys: ``id``, ``name``, ``description``, ``metric_path``,
            ``comparator``, ``threshold``, ``severity``,
            ``target_altitudes``, ``enabled``, ``created_at``,
            ``updated_at``.

    Returns:
        A validated :class:`CustomRuleDefinition` reconstructed from
        the row.

    Raises:
        MalformedRowError: If parsing or validation fails. Non-retryable
            (data corruption is deterministic; retrying re-reads the
            same bad row). The original exception is logged via
            ``safe_error_description``.
    """
    try:
        raw_altitudes = row["target_altitudes"]
        decoded_altitudes: object
        if isinstance(raw_altitudes, str):
            decoded_altitudes = json.loads(raw_altitudes)
        else:
            decoded_altitudes = raw_altitudes
        # Reject anything that is not a list/tuple BEFORE coercing --
        # ``list(dict_value)`` would silently iterate dict keys,
        # ``list(str_value)`` would yield characters, etc.  An
        # explicit type check fails loudly via MalformedRowError
        # rather than producing nonsense ``ProposalAltitude`` lookups
        # downstream.
        if not isinstance(decoded_altitudes, (list, tuple)):
            msg = (
                "target_altitudes must be a list or tuple, got "
                f"{type(decoded_altitudes).__name__}"
            )
            raise TypeError(msg)  # noqa: TRY301 -- caught + remapped one frame up
        altitudes_list = [str(a) for a in decoded_altitudes]

        return CustomRuleDefinition(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            metric_path=row["metric_path"],
            comparator=row["comparator"],
            threshold=row["threshold"],
            severity=row["severity"],
            target_altitudes=tuple(ProposalAltitude(a) for a in altitudes_list),
            enabled=row["enabled"],
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
        )
    except (
        ValidationError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        row_id = (
            str(row.get("id", "<unknown>")) if isinstance(row, dict) else "<unknown>"
        )
        msg = f"Failed to parse custom rule row {row_id!r}"
        logger.warning(
            META_CUSTOM_RULE_FETCH_FAILED,
            row_id=row_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MalformedRowError(msg) from exc
