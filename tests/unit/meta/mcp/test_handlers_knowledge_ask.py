"""Unit tests for the knowledge ``ask`` MCP handler."""

import json
from datetime import UTC, datetime
from typing import cast

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_KNOWLEDGE
from synthorg.knowledge.enums import KnowledgeClaimType, SourceType
from synthorg.knowledge.models import (
    Citation,
    KnowledgeAnswer,
    KnowledgeAnswerClaim,
    WebLocator,
)
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.meta.mcp.handlers.knowledge import _knowledge_ask
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)


def _resolver(*, enabled: bool = True, synthesis: bool = True) -> ConfigResolver:
    """A resolver gating knowledge.enabled / knowledge.synthesis_enabled."""

    async def _get_bool(namespace: str, key: str) -> bool:
        del namespace
        return synthesis if key == "synthesis_enabled" else enabled

    return cast("ConfigResolver", mock_of[ConfigResolver](get_bool=_get_bool))


def _answer() -> KnowledgeAnswer:
    return KnowledgeAnswer(
        query=NotBlankStr("q"),
        answer="A grounded answer with possibly INJECTED text.",
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


def _service_with_ask() -> KnowledgeService:
    async def _ask(
        *,
        query: NotBlankStr,
        project_id: NotBlankStr,
        limit: int | None = None,
    ) -> KnowledgeAnswer:
        del query, limit
        assert project_id == NotBlankStr("proj-1")
        return _answer()

    service: KnowledgeService = mock_of[KnowledgeService](ask=_ask)
    return service


async def test_ask_returns_wrapped_answer() -> None:
    app_state = make_app_state(
        config_resolver=_resolver(),
        slices={KnowledgeStateSlice: {"service": _service_with_ask()}},
    )
    result = await _knowledge_ask(
        app_state=app_state, arguments={"query": "q", "project_id": "proj-1"}
    )
    body = json.loads(result)
    assert body["status"] == "ok"
    # Synthesised prose + claim text are fenced as untrusted corpus-derived output.
    assert f"<{TAG_KNOWLEDGE}>" in body["data"]["answer"]
    assert f"<{TAG_KNOWLEDGE}>" in body["data"]["claims"][0]["text"]
    assert body["data"]["claims"][0]["citations"][0]["chunk_id"] == "chunk-0"


async def test_ask_errors_when_service_absent() -> None:
    app_state = make_app_state(config_resolver=_resolver())
    result = await _knowledge_ask(app_state=app_state, arguments={"query": "q"})
    assert json.loads(result)["status"] == "error"


async def test_ask_errors_on_invalid_args() -> None:
    app_state = make_app_state(
        config_resolver=_resolver(),
        slices={KnowledgeStateSlice: {"service": _service_with_ask()}},
    )
    result = await _knowledge_ask(app_state=app_state, arguments={})
    assert json.loads(result)["status"] == "error"


async def test_ask_503_when_knowledge_disabled() -> None:
    """The live gate 503s ask when knowledge.enabled=false in settings."""
    app_state = make_app_state(
        config_resolver=_resolver(enabled=False),
        slices={KnowledgeStateSlice: {"service": _service_with_ask()}},
    )
    result = await _knowledge_ask(
        app_state=app_state, arguments={"query": "q", "project_id": "proj-1"}
    )
    assert json.loads(result)["status"] == "error"


async def test_ask_503_when_synthesis_disabled() -> None:
    """ask 503s when synthesis_enabled=false even though the service is wired."""
    app_state = make_app_state(
        config_resolver=_resolver(synthesis=False),
        slices={KnowledgeStateSlice: {"service": _service_with_ask()}},
    )
    result = await _knowledge_ask(
        app_state=app_state, arguments={"query": "q", "project_id": "proj-1"}
    )
    assert json.loads(result)["status"] == "error"
