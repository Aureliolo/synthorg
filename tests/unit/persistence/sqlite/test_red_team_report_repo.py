"""Error-path coverage for the SQLite red-team report archive repo.

``_row_to_model`` reconstructs a :class:`RedTeamReportRecord` from a row,
decoding the merged report from the ``report_json`` blob. A corrupt blob
(truncated write, schema-incompatible row) must surface as a
:class:`QueryError` at the persistence boundary rather than a raw
``ValidationError`` leaking through ``query``. The decode is pure, so the
row is driven directly without needing a populated table.
"""

from datetime import UTC, datetime

import aiosqlite
import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.sqlite.red_team_report_repo import (
    SQLiteRedTeamReportArchiveRepository,
)
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit

_RECORDED_AT = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC).isoformat()


def _repo(db: aiosqlite.Connection) -> SQLiteRedTeamReportArchiveRepository:
    return SQLiteRedTeamReportArchiveRepository(
        db, write_context=make_private_write_context()
    )


async def test_row_to_model_raises_query_error_on_corrupt_report_json(
    memory_db: aiosqlite.Connection,
) -> None:
    """A truncated ``report_json`` blob is a boundary QueryError, not a leak."""
    repo = _repo(memory_db)
    corrupt_row = {
        "execution_id": "exec-1",
        "task_id": "task-1",
        "verdict": "block",
        "finding_count": 0,
        "report_summary": "preview",
        "report_json": '{"execution_id": "exec-1", "task_id": "task-1"',
        "recorded_at": _RECORDED_AT,
    }

    with pytest.raises(QueryError, match="Failed to deserialize"):
        repo._row_to_model(corrupt_row)


async def test_row_to_model_raises_query_error_on_unknown_verdict(
    memory_db: aiosqlite.Connection,
) -> None:
    """A verdict value outside the enum is rejected at the boundary."""
    repo = _repo(memory_db)
    bad_verdict_row = {
        "execution_id": "exec-1",
        "task_id": "task-1",
        "verdict": "not-a-verdict",
        "finding_count": 0,
        "report_summary": "preview",
        "report_json": (
            '{"execution_id": "exec-1", "task_id": "task-1", '
            '"findings": [], "summary": "clean"}'
        ),
        "recorded_at": _RECORDED_AT,
    }

    with pytest.raises(QueryError, match="Failed to deserialize"):
        repo._row_to_model(bad_verdict_row)
