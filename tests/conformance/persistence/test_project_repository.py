"""A project survives the round trip, on both backends, with every field set.

The invariant is the whole point of a repository: what was written is what
comes back. It is stated over a FULLY populated project rather than a minimal
one, because the field that broke was optional and every existing test left it
unset: `Project.deadline` is an ISO 8601 string, its column is `TIMESTAMPTZ`,
and the Postgres row mapper coerced the two timestamp columns it knew about
and not this one. So a project carrying a deadline was written successfully
and could never be read again, which took the whole charter intake path down
with a 500 the moment an operator named a date.

SQLite stores the string as a string and round-trips it either way, so this
only fails on one backend, which is exactly what a conformance suite is for.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_DEADLINE = "2026-08-21T00:00:00+00:00"


def _project(project_id: str = "proj-round-trip", **overrides: object) -> Project:
    data: dict[str, object] = {
        "id": as_uuid(project_id),
        "name": NotBlankStr("Browser Falling-Blocks Puzzle Game v1"),
        "description": "A single-player falling-blocks game playable in a browser",
        "lead": NotBlankStr("engineering"),
        "status": ProjectStatus.ACTIVE,
        "created_at": datetime(2026, 8, 18, 21, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 18, 21, tzinfo=UTC),
    }
    data.update(overrides)
    return Project.model_validate(data)


class TestProjectRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        fetched = await backend.projects.get(sid("proj-round-trip"))
        assert fetched is not None
        assert fetched.id == as_uuid("proj-round-trip")
        assert fetched.name == "Browser Falling-Blocks Puzzle Game v1"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.projects.get(sid("no-such-project")) is None

    async def test_a_deadline_survives_the_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project("proj-dated", deadline=_DEADLINE))
        fetched = await backend.projects.get(sid("proj-dated"))
        assert fetched is not None
        assert fetched.deadline is not None
        # Compared as instants, not as text: the column is a timestamp, so a
        # backend is entitled to hand back a different but equivalent
        # spelling. What it is not entitled to do is fail to hand it back.
        assert datetime.fromisoformat(fetched.deadline) == datetime.fromisoformat(
            _DEADLINE
        )

    async def test_no_deadline_stays_absent(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project("proj-undated"))
        fetched = await backend.projects.get(sid("proj-undated"))
        assert fetched is not None
        assert fetched.deadline is None

    async def test_a_dated_project_is_listed(self, backend: PersistenceBackend) -> None:
        # The read that broke was a single get, but a list that skips or
        # raises on the same row hides the same defect one page further on.
        await backend.projects.save(_project("proj-listed", deadline=_DEADLINE))
        listed = await backend.projects.list_items()
        assert any(p.id == as_uuid("proj-listed") for p in listed)

    async def test_a_deadline_can_be_cleared(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project("proj-cleared", deadline=_DEADLINE))
        await backend.projects.save(_project("proj-cleared"))
        fetched = await backend.projects.get(sid("proj-cleared"))
        assert fetched is not None
        assert fetched.deadline is None
