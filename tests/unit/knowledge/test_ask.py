"""In-process round-trip for :meth:`KnowledgeService.ask` (generative RAG).

Exercises the ask surface in-process (InMemoryBackend + fake repos + a
ScriptedProvider, no network): a retrieval-only service 503s, while a service
wired with a synthesiser ingests a corpus and answers with citations that
resolve to retrieved chunks.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.enums import SourceType
from synthorg.knowledge.errors import (
    KnowledgeSynthesisError,
    KnowledgeSynthesisUnavailableError,
)
from synthorg.knowledge.indexer import KnowledgeIndexer
from synthorg.knowledge.retrieval import KnowledgeRetriever
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.synthesis.citation_binder import KnowledgeCitationBinder
from synthorg.knowledge.synthesis.llm_synthesizer import KnowledgeSynthesizer
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.providers.models import CompletionResponse, TokenUsage
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.knowledge._fakes import (
    FakeChunkProvenanceRepository,
    FakeKnowledgeSourceRepository,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, tzinfo=UTC)


def _response(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=10, cost=0.02),
        model="example-medium-001",
    )


async def _service(
    *,
    repo_root: str,
    provider: ScriptedProvider | None = None,
) -> KnowledgeService:
    backend = InMemoryBackend()
    await backend.connect()
    sources = FakeKnowledgeSourceRepository()
    provenance = FakeChunkProvenanceRepository()
    clock = FakeClock(start=_NOW)
    synthesizer = (
        KnowledgeSynthesizer(
            provider=provider,
            model="example-medium-001",
            binder=KnowledgeCitationBinder(),
            clock=clock,
        )
        if provider is not None
        else None
    )
    return KnowledgeService(
        sources=sources,
        indexer=KnowledgeIndexer(backend=backend, provenance=provenance, clock=clock),
        retriever=KnowledgeRetriever(
            backend=backend, sources=sources, provenance=provenance
        ),
        config=KnowledgeConfig(repo_root=repo_root),
        synthesizer=synthesizer,
        clock=clock,
    )


async def test_ask_without_synthesizer_raises_unavailable(tmp_path: Path) -> None:
    service = await _service(repo_root=str(tmp_path))
    with pytest.raises(KnowledgeSynthesisUnavailableError):
        await service.ask(query=NotBlankStr("anything"))


async def test_ask_returns_grounded_cited_answer(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(
        "def login(user):\n    return checkout_token(user)\n", encoding="utf-8"
    )
    payload = json.dumps(
        {
            "answer": "Login returns a checkout token.",
            "claims": [
                {
                    "text": "login returns checkout_token",
                    "claim_type": "fact",
                    "confidence": 0.9,
                    "ref_ids": ["src-0"],
                }
            ],
        }
    )
    provider = ScriptedProvider(response=_response(payload))
    service = await _service(repo_root=str(tmp_path), provider=provider)
    source = await service.ingest(
        source_type=SourceType.REPO,
        uri=NotBlankStr(str(tmp_path)),
        title=NotBlankStr("Repo"),
        project_id=NotBlankStr("proj-1"),
    )

    answer = await service.ask(
        query=NotBlankStr("checkout_token"),
        project_id=NotBlankStr("proj-1"),
    )

    assert answer.query == "checkout_token"
    assert answer.claims[0].citations[0].source_id == source.source_id
    assert answer.chunks_consulted >= 1
    assert provider.call_count == 1


async def test_ask_with_empty_corpus_raises_insufficient_grounding(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(response=_response("{}"))
    service = await _service(repo_root=str(tmp_path), provider=provider)
    with pytest.raises(KnowledgeSynthesisError, match="insufficient grounding"):
        await service.ask(query=NotBlankStr("nothing is indexed"))
    assert provider.call_count == 0
