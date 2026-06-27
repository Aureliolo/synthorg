"""Unit tests for knowledge synthesis: citation binder and LLM synthesiser."""

import json
from datetime import UTC, datetime
from typing import override

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_KNOWLEDGE
from synthorg.knowledge.enums import SourceType
from synthorg.knowledge.errors import KnowledgeSynthesisError
from synthorg.knowledge.models import Citation, KnowledgeHit, WebLocator
from synthorg.knowledge.synthesis.citation_binder import KnowledgeCitationBinder
from synthorg.knowledge.synthesis.llm_synthesizer import KnowledgeSynthesizer
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.cost_recording import (
    CostRecordingContext,
    current_cost_context,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_HASH = "c" * 64


def scripted_response(content: str, *, cost: float = 0.01) -> CompletionResponse:
    """Build a completion response carrying *content* and a token cost."""
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=10, cost=cost),
        model="example-medium-001",
    )


def _hit(index: int, *, text: str = "evidence text") -> KnowledgeHit:
    return KnowledgeHit(
        chunk_text=text,
        relevance_score=0.7,
        citation=Citation(
            source_id=NotBlankStr("source-1"),
            chunk_id=NotBlankStr(f"chunk-{index}"),
            source_type=SourceType.WEB,
            title="Guide",
            uri=NotBlankStr(f"https://src/{index}"),
            locator=WebLocator(
                url=NotBlankStr("https://src"), char_start=0, char_end=5
            ),
            content_hash=_HASH,
        ),
    )


def _synth(provider: ScriptedProvider) -> KnowledgeSynthesizer:
    return KnowledgeSynthesizer(
        provider=provider,
        model="example-medium-001",
        binder=KnowledgeCitationBinder(),
        clock=FakeClock(start=_NOW),
    )


class _CtxCapturingProvider(ScriptedProvider):
    """ScriptedProvider that captures the cost-recording context per call."""

    def __init__(self, payload: str) -> None:
        super().__init__(response=scripted_response(payload))
        self.captured: CostRecordingContext | None = None
        self.was_called = False

    @override
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.was_called = True
        self.captured = current_cost_context()
        return await super().complete(messages, model, tools=tools, config=config)


# -- Citation binder ----------------------------------------------------------


def test_binder_resolves_and_dedupes() -> None:
    hits_by_ref = {"src-0": _hit(0), "src-1": _hit(1)}
    citations = KnowledgeCitationBinder().resolve(
        ("src-0", "src-0", "src-1"), hits_by_ref
    )
    assert [c.chunk_id for c in citations] == ["chunk-0", "chunk-1"]


def test_binder_raises_on_unknown_ref() -> None:
    with pytest.raises(KnowledgeSynthesisError, match="unknown source"):
        KnowledgeCitationBinder().resolve(("src-9",), {"src-0": _hit(0)})


def test_binder_raises_on_empty() -> None:
    with pytest.raises(KnowledgeSynthesisError, match="no sources"):
        KnowledgeCitationBinder().resolve((), {"src-0": _hit(0)})


# -- LLM synthesiser ----------------------------------------------------------


async def test_synthesiser_builds_cited_answer() -> None:
    payload = json.dumps(
        {
            "answer": "Widgets are widely adopted across the corpus.",
            "claims": [
                {
                    "text": "Widgets are widely adopted.",
                    "claim_type": "fact",
                    "confidence": 0.9,
                    "ref_ids": ["src-0"],
                }
            ],
        }
    )
    provider = ScriptedProvider(response=scripted_response(payload, cost=0.05))
    hits = (_hit(0), _hit(1))

    answer, cost = await _synth(provider).synthesize(
        query=NotBlankStr("are widgets adopted?"), hits=hits
    )

    assert cost == pytest.approx(0.05)
    assert answer.query == "are widgets adopted?"
    assert answer.chunks_consulted == 2
    assert answer.synthesis_model == "example-medium-001"
    assert answer.claims[0].citations[0].chunk_id == "chunk-0"
    assert answer.created_at == _NOW


async def test_synthesiser_opens_purpose_scope() -> None:
    payload = json.dumps(
        {
            "answer": "An answer.",
            "claims": [
                {
                    "text": "A claim.",
                    "claim_type": "fact",
                    "confidence": 0.9,
                    "ref_ids": ["src-0"],
                }
            ],
        }
    )
    provider = _CtxCapturingProvider(payload)
    synth = KnowledgeSynthesizer(
        provider=provider,
        model="example-medium-001",
        binder=KnowledgeCitationBinder(),
        clock=FakeClock(start=_NOW),
        cost_tracker=CostTracker(),
    )

    await synth.synthesize(query=NotBlankStr("q"), hits=(_hit(0),))

    assert provider.was_called
    ctx = provider.captured
    assert ctx is not None
    assert ctx.prompt_class_id is PromptPurposeId.KNOWLEDGE_SYNTHESIS


async def test_synthesiser_wraps_chunks_as_untrusted() -> None:
    payload = json.dumps(
        {
            "answer": "An answer.",
            "claims": [
                {
                    "text": "A claim.",
                    "claim_type": "fact",
                    "confidence": 0.8,
                    "ref_ids": ["src-0"],
                }
            ],
        }
    )
    provider = ScriptedProvider(response=scripted_response(payload))
    await _synth(provider).synthesize(
        query=NotBlankStr("q"), hits=(_hit(0, text="INJECTED instructions"),)
    )
    user_prompt = provider.received_messages[0][1].content
    assert user_prompt is not None
    assert f"<{TAG_KNOWLEDGE}>" in user_prompt
    assert "INJECTED instructions" in user_prompt


async def test_synthesiser_truncates_to_max_chunks() -> None:
    payload = json.dumps(
        {
            "answer": "An answer.",
            "claims": [
                {
                    "text": "A claim.",
                    "claim_type": "fact",
                    "confidence": 0.8,
                    "ref_ids": ["src-0"],
                }
            ],
        }
    )
    provider = ScriptedProvider(response=scripted_response(payload))
    synth = KnowledgeSynthesizer(
        provider=provider,
        model="example-medium-001",
        binder=KnowledgeCitationBinder(),
        max_chunks=2,
        clock=FakeClock(start=_NOW),
    )

    answer, _cost = await synth.synthesize(
        query=NotBlankStr("q"), hits=tuple(_hit(i) for i in range(4))
    )

    assert answer.chunks_consulted == 2
    user_prompt = provider.received_messages[0][1].content
    assert user_prompt is not None
    assert "ref_id: src-1" in user_prompt
    assert "ref_id: src-2" not in user_prompt


async def test_synthesiser_rejects_no_hits() -> None:
    provider = ScriptedProvider(response=scripted_response("{}"))
    with pytest.raises(KnowledgeSynthesisError, match="insufficient grounding"):
        await _synth(provider).synthesize(query=NotBlankStr("q"), hits=())
    assert provider.call_count == 0


async def test_synthesiser_rejects_claim_citing_unknown_chunk() -> None:
    payload = json.dumps(
        {
            "answer": "An answer.",
            "claims": [
                {
                    "text": "Unsourced.",
                    "claim_type": "analysis",
                    "confidence": 0.5,
                    "ref_ids": ["src-9"],
                }
            ],
        }
    )
    provider = ScriptedProvider(response=scripted_response(payload))
    with pytest.raises(KnowledgeSynthesisError, match="unknown source"):
        await _synth(provider).synthesize(query=NotBlankStr("q"), hits=(_hit(0),))


async def test_synthesiser_rejects_unparseable_output() -> None:
    provider = ScriptedProvider(response=scripted_response("not json"))
    with pytest.raises(KnowledgeSynthesisError, match="unparseable"):
        await _synth(provider).synthesize(query=NotBlankStr("q"), hits=(_hit(0),))
