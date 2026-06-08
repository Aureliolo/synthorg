"""Row <-> model marshalling for the Postgres approval repository."""

from uuid import UUID

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.evidence import EvidencePackage
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APPROVAL_REPO_FAILED
from synthorg.persistence._shared import coerce_row_timestamp

logger = get_logger(__name__)


def item_save_params(item: ApprovalItem) -> tuple[object, ...]:
    """Flatten an approval item into the positional upsert params.

    Returns:
        The 17-column parameter tuple for ``APPROVALS_UPSERT_SQL``.
    """
    evidence_json = (
        Jsonb(item.evidence_package.model_dump(mode="json"))
        if item.evidence_package is not None
        else None
    )
    return (
        str(item.id),
        item.action_type,
        item.title,
        item.description,
        item.requested_by,
        item.risk_level.value,
        item.source.value,
        item.status.value,
        item.created_at,
        item.expires_at,
        item.decided_at,
        item.decided_by,
        item.decision_reason,
        item.task_id,
        evidence_json,
        Jsonb(item.metadata),
        item.consumed_at,
    )


def row_to_item(row: DictRow) -> ApprovalItem:
    """Convert a Postgres dict row into an :class:`ApprovalItem`.

    Postgres ``TIMESTAMPTZ`` columns return native ``datetime`` objects
    via psycopg, but legacy or migrated rows may carry ISO 8601 strings;
    :func:`coerce_row_timestamp` tolerates both so all timestamps land as
    UTC-aware datetimes.

    Returns:
        Result of type ``ApprovalItem``.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        # Normalise only NULL explicitly; preserve other falsy payloads
        # (e.g. ``[]``, ``""``, ``0``, ``false``) so ``ApprovalItem``'s
        # ``dict[str, str]`` validation rejects them via ``ValidationError``
        # rather than masking corruption as an empty dict.
        raw_metadata = row["metadata"]
        metadata_raw = {} if raw_metadata is None else raw_metadata
        evidence_package = (
            EvidencePackage.model_validate(row["evidence_package"])
            if row["evidence_package"] is not None
            else None
        )
        created_at = coerce_row_timestamp(row["created_at"])
        expires_at = (
            coerce_row_timestamp(row["expires_at"])
            if row["expires_at"] is not None
            else None
        )
        decided_at = (
            coerce_row_timestamp(row["decided_at"])
            if row["decided_at"] is not None
            else None
        )
        consumed_at = (
            coerce_row_timestamp(row["consumed_at"])
            if row["consumed_at"] is not None
            else None
        )
        return ApprovalItem(
            id=UUID(str(row["id"])),
            action_type=str(row["action_type"]),
            title=str(row["title"]),
            description=str(row["description"]),
            requested_by=str(row["requested_by"]),
            risk_level=ApprovalRiskLevel(str(row["risk_level"])),
            source=ApprovalSource(str(row["source"])),
            status=ApprovalStatus(str(row["status"])),
            created_at=created_at,
            expires_at=expires_at,
            decided_at=decided_at,
            decided_by=(
                str(row["decided_by"]) if row["decided_by"] is not None else None
            ),
            decision_reason=(
                str(row["decision_reason"])
                if row["decision_reason"] is not None
                else None
            ),
            task_id=(str(row["task_id"]) if row["task_id"] is not None else None),
            consumed_at=consumed_at,
            evidence_package=evidence_package,
            metadata=metadata_raw,
        )
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        try:
            row_id = str(row["id"]) if row else "<unknown>"
        except TypeError, KeyError:
            row_id = "<unknown>"
        msg = f"Failed to parse approval row {row_id!r}: {safe_error_description(exc)}"
        logger.warning(
            API_APPROVAL_REPO_FAILED,
            row_id=row_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


__all__ = ["item_save_params", "row_to_item"]
