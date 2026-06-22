"""Tests for the long-horizon project-brain REST controller.

Read-only endpoints (list / search / history) are exercised against a fake
:class:`ProjectBrainService` swapped onto ``app_state``. Writes have no REST
surface (agent tool / MCP only), so they are not tested here.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.project_brain.models import (
    BrainEntryKind,
    BrainEntryStatus,
    BrainEntryVersion,
    BrainSearchHit,
    BrainSummary,
)
from synthorg.project_brain.service import ProjectBrainService
from synthorg.project_brain.state import ProjectBrainStateSlice
from tests._shared import LoopAsyncClient, mock_of

_NOW = datetime(2026, 5, 20, tzinfo=UTC)


def _summary(entry_id: str, *, title: str) -> BrainSummary:
    return BrainSummary(
        project_id=NotBlankStr("proj-1"),
        entry_id=NotBlankStr(entry_id),
        revision=1,
        entry_kind=BrainEntryKind.DECISION,
        title=NotBlankStr(title),
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("agent_alice"),
        recorded_at=_NOW,
    )


class _FakeBrainService:
    """Returns canned models for the read endpoints."""

    async def list_current(self, **_: object) -> tuple[BrainSummary, ...]:
        return (_summary("dec-1", title="Adopt Postgres"),)

    async def query(self, **_: object) -> tuple[BrainSearchHit, ...]:
        return (
            BrainSearchHit(
                project_id=NotBlankStr("proj-1"),
                entry_id=NotBlankStr("dec-1"),
                entry_kind=BrainEntryKind.DECISION,
                chunk_text=NotBlankStr("we chose Postgres for MVCC"),
                relevance_score=0.9,
            ),
        )

    async def git_history(self, **_: object) -> tuple[BrainEntryVersion, ...]:
        return (
            BrainEntryVersion(
                commit_hash=NotBlankStr("a" * 40),
                revision=1,
                author=NotBlankStr("agent_alice"),
                committed_at=_NOW,
                summary=NotBlankStr("record decision dec-1"),
            ),
        )


class _PaginatingBrainService:
    """Respects ``offset`` / ``limit`` so page-2 truncation is observable.

    The controller fetches ``limit + 1`` rows and must thread the decoded
    cursor offset into ``list_current``; a controller that drops the offset
    re-reads the first page on every request, so this fake exposes the bug.
    """

    def __init__(self, items: tuple[BrainSummary, ...]) -> None:
        self._items = items

    async def list_current(
        self, *, limit: int, offset: int = 0, **_: object
    ) -> tuple[BrainSummary, ...]:
        return self._items[offset : offset + limit]

    async def git_history(
        self, *, limit: int, offset: int = 0, **_: object
    ) -> tuple[BrainEntryVersion, ...]:
        versions = tuple(
            BrainEntryVersion(
                commit_hash=NotBlankStr(f"{i:040d}"),
                revision=i + 1,
                author=NotBlankStr("agent_alice"),
                committed_at=_NOW,
                summary=NotBlankStr(f"rev {i}"),
            )
            for i in range(len(self._items))
        )
        return versions[offset : offset + limit]


def _as_brain_service(fake: object) -> ProjectBrainService | None:
    """Wrap a read-path fake as a spec'd ``ProjectBrainService`` double.

    The fake duck-types only the read methods a given test exercises; binding
    them onto a ``mock_of[ProjectBrainService]`` autospec satisfies the
    service-resolver's runtime type boundary while preserving behaviour.
    ``None`` passes through so the unwired-service path stays exercisable.
    """
    if fake is None:
        return None
    overrides = {
        method: bound
        for method in ("list_current", "query", "git_history")
        if (bound := getattr(fake, method, None)) is not None
    }
    service: ProjectBrainService = mock_of[ProjectBrainService](**overrides)
    return service


@contextmanager
def _with_brain_service(
    async_test_client: LoopAsyncClient, svc: object
) -> Iterator[None]:
    app_state = async_test_client.app.state.app_state
    original_slice = app_state.slice(ProjectBrainStateSlice)
    app_state.swap_slice(
        ProjectBrainStateSlice.model_construct(service=_as_brain_service(svc))
    )
    try:
        yield
    finally:
        app_state.swap_slice(original_slice)


@pytest.mark.unit
class TestProjectBrainController:
    async def test_not_wired_returns_503(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        with _with_brain_service(async_test_client, None):
            resp = await async_test_client.get("/api/v1/projects/proj-1/brain")
        assert resp.status_code == 503

    async def test_list_entries(self, async_test_client: LoopAsyncClient) -> None:
        with _with_brain_service(async_test_client, _FakeBrainService()):
            resp = await async_test_client.get("/api/v1/projects/proj-1/brain")
        assert resp.status_code == 200
        assert resp.json()["data"][0]["entry_id"] == "dec-1"

    async def test_list_rejects_invalid_entry_kind(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        with _with_brain_service(async_test_client, _FakeBrainService()):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/brain",
                params={"entry_kind": "not_a_kind"},
            )
        assert resp.status_code == 422

    async def test_search_entries(self, async_test_client: LoopAsyncClient) -> None:
        with _with_brain_service(async_test_client, _FakeBrainService()):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/brain/search", params={"q": "postgres"}
            )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["entry_id"] == "dec-1"

    async def test_history(self, async_test_client: LoopAsyncClient) -> None:
        with _with_brain_service(async_test_client, _FakeBrainService()):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/brain/dec-1/history"
            )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["commit_hash"] == "a" * 40

    async def test_pagination_reaches_second_page(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Following the cursor returns the *next* rows, not the overflow row.

        Regression guard: feeding ``limit + 1`` rows to an in-memory slicer
        without threading the decoded offset silently truncates everything
        past the first page.
        """
        items = tuple(_summary(f"dec-{i}", title=f"Decision {i}") for i in range(5))
        with _with_brain_service(async_test_client, _PaginatingBrainService(items)):
            first = await async_test_client.get(
                "/api/v1/projects/proj-1/brain", params={"limit": 2}
            )
            assert first.status_code == 200
            first_body = first.json()
            assert [e["entry_id"] for e in first_body["data"]] == ["dec-0", "dec-1"]
            assert first_body["pagination"]["has_more"] is True
            cursor = first_body["pagination"]["next_cursor"]
            assert cursor is not None

            second = await async_test_client.get(
                "/api/v1/projects/proj-1/brain",
                params={"limit": 2, "cursor": cursor},
            )
        assert second.status_code == 200
        second_body = second.json()
        # The second page must be the *next* two rows; a controller that
        # drops the offset returns only the single overflow row here.
        assert [e["entry_id"] for e in second_body["data"]] == ["dec-2", "dec-3"]
        assert second_body["pagination"]["has_more"] is True

    async def test_history_pagination_reaches_second_page(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Following the history cursor returns the next git revisions."""
        items = tuple(_summary(f"dec-{i}", title=f"Decision {i}") for i in range(5))
        with _with_brain_service(async_test_client, _PaginatingBrainService(items)):
            first = await async_test_client.get(
                "/api/v1/projects/proj-1/brain/dec-1/history", params={"limit": 2}
            )
            assert first.status_code == 200
            first_body = first.json()
            assert [v["revision"] for v in first_body["data"]] == [1, 2]
            assert first_body["pagination"]["has_more"] is True
            cursor = first_body["pagination"]["next_cursor"]
            assert cursor is not None

            second = await async_test_client.get(
                "/api/v1/projects/proj-1/brain/dec-1/history",
                params={"limit": 2, "cursor": cursor},
            )
        assert second.status_code == 200
        assert [v["revision"] for v in second.json()["data"]] == [3, 4]
