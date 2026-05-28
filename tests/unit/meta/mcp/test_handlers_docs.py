# mypy: disable-error-code="explicit-any,unused-awaitable"
"""Unit tests for the living-documentation MCP handlers.

The generic error-path sweep in
:mod:`tests.unit.meta.mcp.test_handler_error_paths` already covers the
``except`` branches of every handler. This module locks in the
docs-specific happy paths, the ``docs:write`` admin guardrail, and
argument validation for the read handlers.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.api.state import AppState
from synthorg.core.enums import DocType
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.errors import DocNotFoundError
from synthorg.docs_engine.models import (
    DocMetadata,
    DocSearchHit,
    DocSummary,
    DocVersion,
    LivingDocument,
    ProseBlock,
)
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.meta.mcp.handlers.docs import (
    _docs_get,
    _docs_history,
    _docs_list,
    _docs_search,
    _docs_write,
)
from tests._shared import make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, tzinfo=UTC)


def _metadata() -> DocMetadata:
    return DocMetadata(
        project_id=NotBlankStr("proj-1"),
        slug=NotBlankStr("q2-status"),
        doc_type=DocType.STATUS_REPORT,
        title=NotBlankStr("Q2 Status"),
        head_commit_sha=NotBlankStr("a" * 40),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _document() -> LivingDocument:
    return LivingDocument(
        slug=NotBlankStr("q2-status"),
        title=NotBlankStr("Q2 Status"),
        doc_type=DocType.STATUS_REPORT,
        author_agent_id=NotBlankStr("agent_alice"),
        body=(ProseBlock(text="body"),),
        created_at=_NOW,
        updated_at=_NOW,
    )


class _FakeDocsService:
    """Captures calls and returns canned models for the handler tests."""

    def __init__(self) -> None:
        self.write_called = False
        self.not_found = False

    async def write_doc(self, **_: Any) -> DocMetadata:
        self.write_called = True
        return _metadata()

    async def read_doc(self, **_: Any) -> LivingDocument:
        if self.not_found:
            msg = "missing"
            raise DocNotFoundError(msg)
        return _document()

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


def _state(svc: _FakeDocsService) -> AppState:
    return make_app_state(slices={DocsStateSlice: {"service": svc}})


class TestDocsWrite:
    async def test_writes_with_admin_guardrails(self) -> None:
        svc = _FakeDocsService()
        result = await _docs_write(
            app_state=_state(svc),
            arguments={
                "confirm": True,
                "reason": "operator authoring",
                "project_id": "proj-1",
                "title": "Q2 Status",
                "doc_type": "status_report",
                "author_agent_id": "agent_alice",
                "body": [{"block_kind": "prose", "text": "body"}],
            },
            actor=make_test_actor(name="admin"),
        )
        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert payload["data"]["slug"] == "q2-status"
        assert svc.write_called is True

    async def test_guardrail_blocks_without_confirm(self) -> None:
        svc = _FakeDocsService()
        result = await _docs_write(
            app_state=_state(svc),
            arguments={
                "project_id": "proj-1",
                "title": "Q2 Status",
                "doc_type": "status_report",
                "author_agent_id": "agent_alice",
                "body": [{"block_kind": "prose", "text": "body"}],
            },
            actor=make_test_actor(name="admin"),
        )
        payload = json.loads(result)
        assert payload["status"] == "error"
        assert svc.write_called is False

    async def test_rejects_invalid_doc_type(self) -> None:
        svc = _FakeDocsService()
        result = await _docs_write(
            app_state=_state(svc),
            arguments={
                "confirm": True,
                "reason": "operator authoring",
                "project_id": "proj-1",
                "title": "Q2 Status",
                "doc_type": "not_a_type",
                "author_agent_id": "agent_alice",
                "body": [{"block_kind": "prose", "text": "body"}],
            },
            actor=make_test_actor(name="admin"),
        )
        payload = json.loads(result)
        assert payload["status"] == "error"
        assert svc.write_called is False


class TestDocsRead:
    async def test_get_returns_document(self) -> None:
        result = await _docs_get(
            app_state=_state(_FakeDocsService()),
            arguments={"project_id": "proj-1", "slug": "q2-status"},
        )
        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert payload["data"]["title"] == "Q2 Status"

    async def test_get_missing_returns_error(self) -> None:
        svc = _FakeDocsService()
        svc.not_found = True
        result = await _docs_get(
            app_state=_state(svc),
            arguments={"project_id": "proj-1", "slug": "ghost"},
        )
        payload = json.loads(result)
        assert payload["status"] == "error"

    async def test_list_returns_summaries(self) -> None:
        result = await _docs_list(
            app_state=_state(_FakeDocsService()),
            arguments={"project_id": "proj-1"},
        )
        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert payload["data"][0]["slug"] == "q2-status"

    async def test_search_returns_hits(self) -> None:
        result = await _docs_search(
            app_state=_state(_FakeDocsService()),
            arguments={"project_id": "proj-1", "query": "checkout"},
        )
        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert payload["data"][0]["doc_slug"] == "q2-status"

    async def test_history_returns_versions(self) -> None:
        result = await _docs_history(
            app_state=_state(_FakeDocsService()),
            arguments={"project_id": "proj-1", "slug": "q2-status"},
        )
        payload = json.loads(result)
        assert payload["status"] == "ok"
        assert payload["data"][0]["commit_sha"] == "b" * 40
