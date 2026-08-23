"""The scaling cut, run over data rather than an empty database.

Two things outlive the deleted subsystem in a database that had it, and the
schema-drift gate can see neither: it builds from empty and compares shapes,
and neither of these changes a shape.

The first is ``hiring_requests.payload``. ``HiringRequest`` forbids extra keys,
so a row still carrying ``agent_delegate`` fails validation on read, and
``_query_rows`` converts one bad row into a ``QueryError`` for the whole page,
which is the page the review-staffing sweep reads. The shape that matters is
the key present holding JSON ``null``: the writer calls ``model_dump`` without
``exclude_none``, so that is what almost every stored row looks like, and
SQLite's ``json_extract`` answers SQL NULL for it exactly as it does for a key
that was never there.

The second is the approvals the deleted gate raised. It wrote real PENDING rows
into the shared ``approvals`` table under ``scaling:hire`` / ``scaling:prune``.
Nothing left can act on one: the level-triggered orphan sweep keys on
``task_id`` and these carry none, and delete-time retirement fires on a row
being removed rather than a subsystem. Expired rather than rejected, for the
reason the retirement path gives: a rejection is a reviewer's verdict, and
nobody made one.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synthorg.hr.models import HiringRequest
from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260823000000_hiring_request_drop_agent_delegate.sql"

_STAMP = "2026-08-01T09:00:00+00:00"

_INSERT_REQUEST = (
    "INSERT INTO hiring_requests "
    "(id, status, requested_by, department, role, created_at, payload) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

_INSERT_APPROVAL = (
    "INSERT INTO approvals "
    "(id, action_type, title, description, requested_by, status, created_at) "
    "VALUES (?, ?, ?, ?, ?, 'pending', ?)"
)


def _payload(request_id: str, role: str, **extra: object) -> str:
    """Build a stored hiring-request payload, optionally with dead keys.

    Args:
        request_id: The row's id, mirrored into the payload as the writer does.
        role: Desired role.
        extra: Keys the current model no longer declares.

    Returns:
        The payload as the repository would have written it.
    """
    body: dict[str, object] = {
        "id": request_id,
        "requested_by": "staffing",
        "department": "Engineering",
        "role": role,
        "required_skills": [],
        "reason": "Gate role unstaffed",
        "budget_limit_monthly": None,
        "template_name": None,
        "status": "pending",
        "created_at": _STAMP,
        "candidates": [],
        "selected_candidate_id": None,
        "approval_id": None,
        "bound_model_ref": None,
    }
    body.update(extra)
    return json.dumps(body, sort_keys=True)


#: The three shapes a stored payload can take for a dropped optional field.
#: ``null`` is first because it is the one the writer actually produces:
#: ``model_dump(mode="json")`` carries no ``exclude_none``, so an unset
#: ``agent_delegate`` was serialised as an explicit null on every row.
_SEEDED_REQUESTS: tuple[tuple[str, str, dict[str, object]], ...] = (
    ("11111111-1111-4111-8111-111111111111", "Reviewer", {"agent_delegate": None}),
    ("22222222-2222-4222-8222-222222222222", "Red Team", {"agent_delegate": "Bob"}),
    ("33333333-3333-4333-8333-333333333333", "Developer", {}),
)

#: Approvals the deleted gate could have raised, plus one that must not move.
_SEEDED_APPROVALS: tuple[tuple[str, str], ...] = (
    ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "scaling:hire"),
    ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "scaling:prune"),
    ("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "org:hire"),
)


def _revisions_before(into: Path) -> Path:
    """Copy the SQLite revisions preceding the one under test into *into*.

    Strictly preceding, not "all but this one": revisions apply in name order,
    so holding out only the revision under test would run it against a schema
    from its own future.

    Args:
        into: Directory to populate.

    Returns:
        The populated revisions directory.
    """
    into.mkdir(parents=True, exist_ok=True)
    for source in sorted(migrations.revisions_dir("sqlite").glob("*.sql")):
        if source.name < _REVISION:
            (into / source.name).write_bytes(source.read_bytes())
    return into


def _add_the_revision(into: Path) -> None:
    """Copy the revision under test into an existing revisions directory."""
    source = migrations.revisions_dir("sqlite") / _REVISION
    (into / _REVISION).write_bytes(source.read_bytes())


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection with references enforced, like the app's."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _seed_pre_cut(conn: sqlite3.Connection) -> None:
    """Write the rows an installation would hold before the scaling cut."""
    for request_id, role, extra in _SEEDED_REQUESTS:
        conn.execute(
            _INSERT_REQUEST,
            (
                request_id,
                "pending",
                "staffing",
                "Engineering",
                role,
                _STAMP,
                _payload(request_id, role, **extra),
            ),
        )
    for approval_id, action_type in _SEEDED_APPROVALS:
        conn.execute(
            _INSERT_APPROVAL,
            (
                approval_id,
                action_type,
                f"Scaling: {action_type}",
                "Raised before the subsystem was removed",
                "scaling_service",
                _STAMP,
            ),
        )
    conn.commit()


@pytest.fixture
async def migrated(tmp_path: Path) -> Path:
    """Seed the pre-cut schema and data, then migrate.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    url = migrations.to_sqlite_url(str(db_path))
    await migrations.migrate_apply(url, revisions_path=revisions)

    with _connect(db_path) as conn:
        _seed_pre_cut(conn)

    _add_the_revision(revisions)
    await migrations.migrate_apply(url, revisions_path=revisions)
    return db_path


def _stored_payload(conn: sqlite3.Connection, request_id: str) -> dict[str, object]:
    """Read one hiring request's payload back as a mapping.

    Returns:
        The decoded payload.
    """
    row = conn.execute(
        "SELECT payload FROM hiring_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    assert row is not None, f"seeded row {request_id} is missing"
    decoded: dict[str, object] = json.loads(str(row[0]))
    return decoded


class TestTheDroppedFieldLeavesEveryRow:
    """A key the model forbids has to go, whatever value it held."""

    @pytest.mark.parametrize(
        "request_id",
        [seeded[0] for seeded in _SEEDED_REQUESTS],
        ids=["null-valued", "value-set", "key-absent"],
    )
    def test_the_payload_no_longer_carries_it(
        self, migrated: Path, request_id: str
    ) -> None:
        with _connect(migrated) as conn:
            payload = _stored_payload(conn, request_id)
        assert "agent_delegate" not in payload

    @pytest.mark.parametrize(
        "request_id",
        [seeded[0] for seeded in _SEEDED_REQUESTS],
        ids=["null-valued", "value-set", "key-absent"],
    )
    def test_the_row_validates_against_the_current_model(
        self, migrated: Path, request_id: str
    ) -> None:
        """The point of the revision: ``extra="forbid"`` accepts the row.

        Asserted through the real model rather than a key check, because that
        is the read the staffing sweep performs and the one that was failing.
        """
        with _connect(migrated) as conn:
            payload = _stored_payload(conn, request_id)
        assert str(HiringRequest.model_validate(payload).id) == request_id

    def test_nothing_else_in_the_payload_moves(self, migrated: Path) -> None:
        """Removing one key must not rewrite the operator's own values."""
        request_id, role, _ = _SEEDED_REQUESTS[0]
        with _connect(migrated) as conn:
            payload = _stored_payload(conn, request_id)
        assert payload["role"] == role
        assert payload["reason"] == "Gate role unstaffed"
        assert payload["requested_by"] == "staffing"


class TestTheOrphanedApprovalsAreClosed:
    """An approval whose subsystem is gone has nothing left to decide."""

    @pytest.mark.parametrize(
        "approval_id",
        [seeded[0] for seeded in _SEEDED_APPROVALS[:2]],
        ids=["hire", "prune"],
    )
    def test_a_pending_scaling_approval_is_expired(
        self, migrated: Path, approval_id: str
    ) -> None:
        with _connect(migrated) as conn:
            row = conn.execute(
                "SELECT status, decided_at, decided_by FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        # Expired, and undecided: nobody made a verdict, so recording one
        # would attribute a decision to a reviewer who never saw it.
        assert row == ("expired", None, None)

    def test_an_unrelated_pending_approval_is_untouched(self, migrated: Path) -> None:
        """``scaling:`` is a prefix, and the sweep must not widen past it."""
        approval_id = _SEEDED_APPROVALS[2][0]
        with _connect(migrated) as conn:
            row = conn.execute(
                "SELECT status FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        assert row == ("pending",)
