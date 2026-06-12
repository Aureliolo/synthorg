"""Tests for the living-documentation REST controller.

Read-only endpoints (list / get / search / history) are exercised
against a fake :class:`DocsService` swapped onto ``app_state``. Writes
have no REST surface (agent tool / MCP only), so they are not tested
here.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.errors import DocNotFoundError
from synthorg.docs_engine.models import (
    DocSearchHit,
    DocSummary,
    DocVersion,
    LivingDocument,
    ProseBlock,
)
from synthorg.docs_engine.service import DocsService
from synthorg.docs_engine.state import DocsStateSlice
from tests._shared import LoopAsyncClient, mock_of

_NOW = datetime(2026, 5, 20, tzinfo=UTC)


class _FakeDocsService:
    """Returns canned models; raises ``DocNotFoundError`` for ``ghost``."""

    async def list_docs(self, **_: object) -> tuple[DocSummary, ...]:
        return (
            DocSummary(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr("q2-status"),
                title=NotBlankStr("Q2 Status"),
                doc_type=DocType.STATUS_REPORT,
                updated_at=_NOW,
            ),
        )

    async def read_doc(
        self, *, project_id: NotBlankStr, slug: NotBlankStr, **_: object
    ) -> LivingDocument:
        if slug == "ghost":
            msg = f"living doc {project_id!r}/{slug!r} not found"
            raise DocNotFoundError(msg)
        return LivingDocument(
            slug=slug,
            title=NotBlankStr("Q2 Status"),
            doc_type=DocType.STATUS_REPORT,
            author_agent_id=NotBlankStr("agent_alice"),
            body=(ProseBlock(text="body"),),
            created_at=_NOW,
            updated_at=_NOW,
        )

    async def search(self, **_: object) -> tuple[DocSearchHit, ...]:
        return (
            DocSearchHit(
                project_id=NotBlankStr("proj-1"),
                doc_slug=NotBlankStr("q2-status"),
                doc_type=DocType.STATUS_REPORT,
                chunk_text=NotBlankStr("checkout improved"),
                relevance_score=0.9,
            ),
        )

    async def history(self, **_: object) -> tuple[DocVersion, ...]:
        return (
            DocVersion(
                commit_sha=NotBlankStr("b" * 40),
                author_agent_id=NotBlankStr("docs_engine"),
                committed_at=_NOW,
                summary=NotBlankStr("write q2-status"),
            ),
        )


class _PaginatingDocsService:
    """Respects ``offset`` / ``limit`` so page-2 truncation is observable.

    A controller that drops the decoded cursor offset re-reads the first page
    on every request, so this fake (which honours the offset) surfaces the bug.
    """

    def __init__(self, items: tuple[DocSummary, ...]) -> None:
        self._items = items

    async def list_docs(
        self, *, limit: int, offset: int = 0, **_: object
    ) -> tuple[DocSummary, ...]:
        return self._items[offset : offset + limit]


def _as_docs_service(fake: object) -> DocsService | None:
    """Wrap a read-path fake as a spec'd ``DocsService`` double.

    The fake duck-types only the read methods a given test exercises; binding
    them onto a ``mock_of[DocsService]`` autospec keeps the service-resolver's
    runtime type boundary satisfied while preserving the fake's behaviour.
    ``None`` passes through so the unwired-service path stays exercisable.
    """
    if fake is None:
        return None
    overrides = {
        method: bound
        for method in ("list_docs", "read_doc", "search", "history")
        if (bound := getattr(fake, method, None)) is not None
    }
    service: DocsService = mock_of[DocsService](**overrides)
    return service


@contextmanager
def _with_docs_service(
    async_test_client: LoopAsyncClient, svc: object
) -> Iterator[None]:
    app_state = async_test_client.app.state.app_state
    original_slice = app_state.slice(DocsStateSlice)
    app_state.swap_slice(DocsStateSlice.model_construct(service=_as_docs_service(svc)))
    try:
        yield
    finally:
        app_state.swap_slice(original_slice)


@pytest.mark.unit
class TestProjectDocsController:
    async def test_not_wired_returns_503(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/projects/proj-1/docs")
        assert resp.status_code == 503

    async def test_list_docs(self, async_test_client: LoopAsyncClient) -> None:
        with _with_docs_service(async_test_client, _FakeDocsService()):
            resp = await async_test_client.get("/api/v1/projects/proj-1/docs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["slug"] == "q2-status"

    async def test_list_accepts_valid_doc_type(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # The query param is the closed DocType enum, so a member value
        # parses cleanly at the boundary.
        with _with_docs_service(async_test_client, _FakeDocsService()):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/docs",
                params={"doc_type": DocType.RUN_NARRATIVE.value},
            )
        assert resp.status_code == 200

    async def test_list_rejects_invalid_doc_type(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # An out-of-enum value fails Litestar's query-param validation for
        # the closed DocType enum, surfacing a 400 before the handler runs.
        with _with_docs_service(async_test_client, _FakeDocsService()):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/docs", params={"doc_type": "not_a_type"}
            )
        assert resp.status_code == 400

    async def test_get_doc(self, async_test_client: LoopAsyncClient) -> None:
        with _with_docs_service(async_test_client, _FakeDocsService()):
            resp = await async_test_client.get("/api/v1/projects/proj-1/docs/q2-status")
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Q2 Status"

    async def test_get_doc_not_found(self, async_test_client: LoopAsyncClient) -> None:
        with _with_docs_service(async_test_client, _FakeDocsService()):
            resp = await async_test_client.get("/api/v1/projects/proj-1/docs/ghost")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_search_docs(self, async_test_client: LoopAsyncClient) -> None:
        with _with_docs_service(async_test_client, _FakeDocsService()):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/docs/search", params={"q": "checkout"}
            )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["doc_slug"] == "q2-status"

    async def test_history(self, async_test_client: LoopAsyncClient) -> None:
        with _with_docs_service(async_test_client, _FakeDocsService()):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/docs/q2-status/history"
            )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["commit_sha"] == "b" * 40

    async def test_pagination_reaches_second_page(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Following the cursor returns the next page, not the overflow row."""
        items = tuple(
            DocSummary(
                project_id=NotBlankStr("proj-1"),
                slug=NotBlankStr(f"doc-{i}"),
                title=NotBlankStr(f"Doc {i}"),
                doc_type=DocType.STATUS_REPORT,
                updated_at=_NOW,
            )
            for i in range(5)
        )
        with _with_docs_service(async_test_client, _PaginatingDocsService(items)):
            first = await async_test_client.get(
                "/api/v1/projects/proj-1/docs", params={"limit": 2}
            )
            assert first.status_code == 200
            first_body = first.json()
            assert [d["slug"] for d in first_body["data"]] == ["doc-0", "doc-1"]
            assert first_body["pagination"]["has_more"] is True
            cursor = first_body["pagination"]["next_cursor"]
            assert cursor is not None
            second = await async_test_client.get(
                "/api/v1/projects/proj-1/docs",
                params={"limit": 2, "cursor": cursor},
            )
        assert second.status_code == 200
        second_body = second.json()
        assert [d["slug"] for d in second_body["data"]] == ["doc-2", "doc-3"]
        assert second_body["pagination"]["has_more"] is True
