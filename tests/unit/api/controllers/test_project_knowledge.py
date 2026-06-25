"""Tests for the knowledge-substrate REST controllers.

Read-only endpoints are exercised against a fake :class:`KnowledgeService`
swapped onto ``app_state``. The pagination cases walk past the first page to
guard against the ``limit + 1`` / in-memory-slice truncation that drops every
row beyond page one when the decoded cursor offset is not threaded into the
service.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.enums import KnowledgeClaimType, SourceStatus, SourceType
from synthorg.knowledge.errors import (
    KnowledgeSynthesisError,
    KnowledgeSynthesisUnavailableError,
)
from synthorg.knowledge.models import (
    Citation,
    KnowledgeAnswer,
    KnowledgeAnswerClaim,
    KnowledgeSource,
    WebLocator,
)
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.state import KnowledgeStateSlice
from tests._shared import LoopAsyncClient, mock_of

_NOW = datetime(2026, 5, 20, tzinfo=UTC)


def _answer(query: str) -> KnowledgeAnswer:
    return KnowledgeAnswer(
        query=NotBlankStr(query),
        answer="A grounded answer.",
        claims=(
            KnowledgeAnswerClaim(
                text="A cited claim.",
                claim_type=KnowledgeClaimType.FACT,
                citations=(
                    Citation(
                        source_id=NotBlankStr("source-1"),
                        chunk_id=NotBlankStr("chunk-0"),
                        source_type=SourceType.WEB,
                        title="Guide",
                        uri=NotBlankStr("https://src"),
                        locator=WebLocator(
                            url=NotBlankStr("https://src"), char_start=0, char_end=5
                        ),
                        content_hash="c" * 64,
                    ),
                ),
                confidence=0.9,
            ),
        ),
        chunks_consulted=1,
        synthesis_model=NotBlankStr("example-medium-001"),
        created_at=_NOW,
    )


class _AnsweringKnowledgeService:
    """Answers via ``ask`` (happy path) or raises a synthesis error."""

    def __init__(
        self, *, unavailable: bool = False, synthesis_error: bool = False
    ) -> None:
        self._unavailable = unavailable
        self._synthesis_error = synthesis_error

    async def ask(self, *, query: NotBlankStr, **_: object) -> KnowledgeAnswer:
        if self._unavailable:
            msg = "knowledge synthesis is not configured"
            raise KnowledgeSynthesisUnavailableError(msg)
        if self._synthesis_error:
            msg = "synthesiser returned unparseable output"
            raise KnowledgeSynthesisError(msg)
        return _answer(query)


def _source(source_id: str, *, project_id: str | None) -> KnowledgeSource:
    return KnowledgeSource(
        source_id=NotBlankStr(source_id),
        source_type=SourceType.REPO,
        project_id=NotBlankStr(project_id) if project_id is not None else None,
        uri=NotBlankStr(f"repo/{source_id}"),
        title=NotBlankStr(f"Source {source_id}"),
        content_hash="a" * 64,
        status=SourceStatus.INDEXED,
        created_at=_NOW,
        updated_at=_NOW,
        last_indexed_at=_NOW,
    )


class _PaginatingKnowledgeService:
    """Respects ``offset`` / ``limit`` so page-2 truncation is observable.

    A controller that drops the decoded cursor offset re-reads the first page
    on every request, so this fake (which honours the offset) surfaces the bug.
    """

    def __init__(self, items: tuple[KnowledgeSource, ...]) -> None:
        self._items = items

    async def list_sources(
        self, *, limit: int, offset: int = 0, **_: object
    ) -> tuple[KnowledgeSource, ...]:
        return self._items[offset : offset + limit]


def _as_knowledge_service(fake: object) -> KnowledgeService | None:
    """Wrap a read-path fake as a spec'd ``KnowledgeService`` double.

    The fake duck-types only the read methods a given test exercises; binding
    them onto a ``mock_of[KnowledgeService]`` autospec satisfies the
    service-resolver's runtime type boundary while preserving behaviour.
    ``None`` passes through so the unwired-service path stays exercisable.
    """
    if fake is None:
        return None
    overrides = {
        method: bound
        for method in ("list_sources", "list_global_sources", "query", "ask")
        if (bound := getattr(fake, method, None)) is not None
    }
    service: KnowledgeService = mock_of[KnowledgeService](**overrides)
    return service


@contextmanager
def _with_knowledge_service(
    async_test_client: LoopAsyncClient, svc: object
) -> Iterator[None]:
    app_state = async_test_client.app.state.app_state
    original_slice = app_state.slice(KnowledgeStateSlice)
    app_state.swap_slice(
        KnowledgeStateSlice.model_construct(service=_as_knowledge_service(svc))
    )
    try:
        yield
    finally:
        app_state.swap_slice(original_slice)


@pytest.mark.unit
class TestProjectKnowledgeController:
    async def test_not_wired_returns_503(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        with _with_knowledge_service(async_test_client, None):
            resp = await async_test_client.get("/api/v1/projects/proj-1/knowledge")
        assert resp.status_code == 503

    async def test_ask_returns_cited_answer(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        with _with_knowledge_service(async_test_client, _AnsweringKnowledgeService()):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/knowledge/ask", params={"q": "a question"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["query"] == "a question"
        assert body["data"]["claims"][0]["citations"][0]["chunk_id"] == "chunk-0"

    async def test_ask_returns_503_when_synthesis_unavailable(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        with _with_knowledge_service(
            async_test_client, _AnsweringKnowledgeService(unavailable=True)
        ):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/knowledge/ask", params={"q": "a question"}
            )
        assert resp.status_code == 503

    async def test_ask_returns_500_on_synthesis_error(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        with _with_knowledge_service(
            async_test_client, _AnsweringKnowledgeService(synthesis_error=True)
        ):
            resp = await async_test_client.get(
                "/api/v1/projects/proj-1/knowledge/ask", params={"q": "a question"}
            )
        assert resp.status_code == 500
        assert (
            resp.json()["error_detail"]["error_code"]
            == ErrorCode.KNOWLEDGE_SYNTHESIS_ERROR
        )

    async def test_list_sources_reaches_second_page(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Following the cursor returns the next page, not the overflow row."""
        items = tuple(_source(f"src-{i}", project_id="proj-1") for i in range(5))
        with _with_knowledge_service(
            async_test_client, _PaginatingKnowledgeService(items)
        ):
            first = await async_test_client.get(
                "/api/v1/projects/proj-1/knowledge", params={"limit": 2}
            )
            assert first.status_code == 200
            first_body = first.json()
            assert [s["source_id"] for s in first_body["data"]] == ["src-0", "src-1"]
            assert first_body["pagination"]["has_more"] is True
            cursor = first_body["pagination"]["next_cursor"]
            assert cursor is not None
            second = await async_test_client.get(
                "/api/v1/projects/proj-1/knowledge",
                params={"limit": 2, "cursor": cursor},
            )
        assert second.status_code == 200
        second_body = second.json()
        assert [s["source_id"] for s in second_body["data"]] == ["src-2", "src-3"]
        assert second_body["pagination"]["has_more"] is True


@pytest.mark.unit
class TestGlobalKnowledgeController:
    async def test_list_global_reaches_second_page(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """The global (project-less) listing walks past page 1 via the cursor."""
        items = tuple(_source(f"g-{i}", project_id=None) for i in range(5))
        with _with_knowledge_service(
            async_test_client, _PaginatingKnowledgeService(items)
        ):
            first = await async_test_client.get(
                "/api/v1/knowledge", params={"limit": 2}
            )
            assert first.status_code == 200
            first_body = first.json()
            assert [s["source_id"] for s in first_body["data"]] == ["g-0", "g-1"]
            cursor = first_body["pagination"]["next_cursor"]
            assert cursor is not None
            second = await async_test_client.get(
                "/api/v1/knowledge", params={"limit": 2, "cursor": cursor}
            )
        assert second.status_code == 200
        second_body = second.json()
        assert [s["source_id"] for s in second_body["data"]] == ["g-2", "g-3"]
        assert second_body["pagination"]["has_more"] is True
