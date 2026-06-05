# module-kind: declarative
"""SQL constants for the Postgres approval repository."""

from typing import LiteralString

SELECT_COLS: LiteralString = (
    "id, action_type, title, description, requested_by, risk_level, "
    "source, status, created_at, expires_at, decided_at, decided_by, "
    "decision_reason, task_id, evidence_package, metadata, consumed_at"
)

APPROVALS_UPSERT_SQL: LiteralString = f"""
    INSERT INTO approvals ({SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        action_type = EXCLUDED.action_type,
        title = EXCLUDED.title,
        description = EXCLUDED.description,
        requested_by = EXCLUDED.requested_by,
        risk_level = EXCLUDED.risk_level,
        source = EXCLUDED.source,
        status = EXCLUDED.status,
        expires_at = EXCLUDED.expires_at,
        decided_at = EXCLUDED.decided_at,
        decided_by = EXCLUDED.decided_by,
        decision_reason = EXCLUDED.decision_reason,
        task_id = EXCLUDED.task_id,
        evidence_package = EXCLUDED.evidence_package,
        metadata = EXCLUDED.metadata,
        consumed_at = COALESCE(approvals.consumed_at, EXCLUDED.consumed_at)
"""  # noqa: S608 -- column list is compile-time constant

__all__ = ["APPROVALS_UPSERT_SQL", "SELECT_COLS"]
