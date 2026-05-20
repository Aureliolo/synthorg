"""Tests for the living-documentation REST controller.

Read-only endpoints (list / get / search / history) are exercised
against a fake :class:`DocsService` swapped onto ``app_state``. Writes
have no REST surface (agent tool / MCP only), so they are not tested
here.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.core.enums import DocType
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.errors import DocNotFoundError
from synthorg.docs_engine.models import (
    DocSearchHit,
    DocSummary,
    DocVersion,
    LivingDocument,
    ProseBlock,
)

_NOW = datetime(2026, 5, 20, tzinfo=UTC)


class _FakeDocsService:
    """Returns canned models; raises ``DocNotFoundError`` for ``ghost``."""

    async def list_docs(self, **_: Any) -> tuple[DocSummary, ...]:
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
        self, *, project_id: NotBlankStr, slug: NotBlankStr, **_: Any
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

    async def search(self, **_: Any) -> tuple[DocSearchHit, ...]:
        return (
            DocSearchHit(
                project_id=NotBlankStr("proj-1"),
                doc_slug=NotBlankStr("q2-status"),
                doc_type=DocType.STATUS_REPORT,
                chunk_text=NotBlankStr("checkout improved"),
                relevance_score=0.9,
            ),
        )

    async def history(self, **_: Any) -> tuple[DocVersion, ...]:
        return (
            DocVersion(
                commit_sha=NotBlankStr("b" * 40),
                author_agent_id=NotBlankStr("docs_engine"),
                committed_at=_NOW,
                summary=NotBlankStr("write q2-status"),
            ),
        )


@contextmanager
def _with_docs_service(test_client: TestClient[Any], svc: object) -> Iterator[None]:
    app_state = test_client.app.state.app_state
    original = app_state._docs_service
    app_state._docs_service = svc
    try:
        yield
    finally:
        app_state._docs_service = original


@pytest.mark.unit
class TestProjectDocsController:
    def test_not_wired_returns_404(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/projects/proj-1/docs")
        assert resp.status_code == 404

    def test_list_docs(self, test_client: TestClient[Any]) -> None:
        with _with_docs_service(test_client, _FakeDocsService()):
            resp = test_client.get("/api/v1/projects/proj-1/docs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["slug"] == "q2-status"

    def test_list_rejects_invalid_doc_type(self, test_client: TestClient[Any]) -> None:
        with _with_docs_service(test_client, _FakeDocsService()):
            resp = test_client.get(
                "/api/v1/projects/proj-1/docs", params={"doc_type": "not_a_type"}
            )
        assert resp.status_code == 422

    def test_get_doc(self, test_client: TestClient[Any]) -> None:
        with _with_docs_service(test_client, _FakeDocsService()):
            resp = test_client.get("/api/v1/projects/proj-1/docs/q2-status")
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "Q2 Status"

    def test_get_doc_not_found(self, test_client: TestClient[Any]) -> None:
        with _with_docs_service(test_client, _FakeDocsService()):
            resp = test_client.get("/api/v1/projects/proj-1/docs/ghost")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_search_docs(self, test_client: TestClient[Any]) -> None:
        with _with_docs_service(test_client, _FakeDocsService()):
            resp = test_client.get(
                "/api/v1/projects/proj-1/docs/search", params={"q": "checkout"}
            )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["doc_slug"] == "q2-status"

    def test_history(self, test_client: TestClient[Any]) -> None:
        with _with_docs_service(test_client, _FakeDocsService()):
            resp = test_client.get("/api/v1/projects/proj-1/docs/q2-status/history")
        assert resp.status_code == 200
        assert resp.json()["data"][0]["commit_sha"] == "b" * 40
