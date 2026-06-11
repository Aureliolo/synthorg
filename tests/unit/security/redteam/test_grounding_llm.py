"""Unit tests for the substrate checker's LLM plumbing.

These parsers are the frontline defence against malformed LLM tool
output: the ``bool``-as-numeric guard stops a JSON ``true`` from being
read as ``confidence=1.0`` (which would route to HIGH and BLOCK), the
control-character strip prevents crafted claims from injecting into log
output, and the length / count caps bound prompt and finding size. They
are exercised here directly rather than only through the happy-path
checker tests.
"""

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.prompt_safety import TAG_TASK_DATA, TAG_UNTRUSTED_ARTIFACT
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import CompletionResponse, TokenUsage, ToolCall
from synthorg.security.redteam.grounding._llm import (
    EXTRACT_CLAIMS_TOOL_NAME,
    GROUNDING_VERDICT_TOOL_NAME,
    MAX_CLAIM_CHARS,
    MAX_CLAIMS,
    MAX_DELIVERABLE_CHARS,
    build_entailment_messages,
    build_extraction_messages,
    parse_extracted_claims,
    parse_grounding_verdict,
)
from tests._shared import JsonDict

pytestmark = pytest.mark.unit

_MODEL = "example-medium-001"


def _response(tool_name: str | None, arguments: JsonDict) -> CompletionResponse:
    calls = (
        (ToolCall(id="x", name=tool_name, arguments=arguments),)
        if tool_name is not None
        else ()
    )
    return CompletionResponse(
        content=None if calls else "I did not call the tool.",
        tool_calls=calls,
        finish_reason=FinishReason.TOOL_USE if calls else FinishReason.STOP,
        usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
        model=_MODEL,
    )


class TestParseGroundingVerdict:
    def test_valid_verdict_returns_label_and_confidence(self) -> None:
        response = _response(
            GROUNDING_VERDICT_TOOL_NAME,
            {"verdict": "unsupported", "confidence": 0.9, "reason": "r"},
        )
        verdict = parse_grounding_verdict(response)
        assert verdict is not None
        assert verdict[0] == "unsupported"
        assert verdict[1] == pytest.approx(0.9)

    def test_bool_confidence_is_rejected(self) -> None:
        # JSON `true` is an int subclass; without the bool guard it would
        # read as confidence 1.0 -> HIGH -> BLOCK.
        response = _response(
            GROUNDING_VERDICT_TOOL_NAME,
            {"verdict": "unsupported", "confidence": True, "reason": "r"},
        )
        assert parse_grounding_verdict(response) is None

    def test_confidence_above_one_is_clamped(self) -> None:
        response = _response(
            GROUNDING_VERDICT_TOOL_NAME,
            {"verdict": "unsupported", "confidence": 1.5, "reason": "r"},
        )
        verdict = parse_grounding_verdict(response)
        assert verdict is not None
        assert verdict[1] == pytest.approx(1.0)

    def test_confidence_below_zero_is_clamped(self) -> None:
        response = _response(
            GROUNDING_VERDICT_TOOL_NAME,
            {"verdict": "supported", "confidence": -0.2, "reason": "r"},
        )
        verdict = parse_grounding_verdict(response)
        assert verdict is not None
        assert verdict[1] == pytest.approx(0.0)

    def test_int_confidence_is_accepted_as_float(self) -> None:
        response = _response(
            GROUNDING_VERDICT_TOOL_NAME,
            {"verdict": "unsupported", "confidence": 1, "reason": "r"},
        )
        verdict = parse_grounding_verdict(response)
        assert verdict is not None
        assert verdict[0] == "unsupported"
        assert verdict[1] == pytest.approx(1.0)

    def test_unknown_verdict_label_returns_none(self) -> None:
        response = _response(
            GROUNDING_VERDICT_TOOL_NAME,
            {"verdict": "maybe", "confidence": 0.9, "reason": "r"},
        )
        assert parse_grounding_verdict(response) is None

    def test_string_confidence_returns_none(self) -> None:
        response = _response(
            GROUNDING_VERDICT_TOOL_NAME,
            {"verdict": "unsupported", "confidence": "high", "reason": "r"},
        )
        assert parse_grounding_verdict(response) is None

    def test_no_tool_call_returns_none(self) -> None:
        assert parse_grounding_verdict(_response(None, {})) is None

    def test_other_tool_name_returns_none(self) -> None:
        response = _response(
            EXTRACT_CLAIMS_TOOL_NAME,
            {"verdict": "unsupported", "confidence": 0.9, "reason": "r"},
        )
        assert parse_grounding_verdict(response) is None


class TestParseExtractedClaims:
    def test_valid_claims_returned(self) -> None:
        response = _response(EXTRACT_CLAIMS_TOOL_NAME, {"claims": ["a", "b"]})
        assert parse_extracted_claims(response) == ("a", "b")

    def test_claims_not_a_list_returns_empty(self) -> None:
        response = _response(EXTRACT_CLAIMS_TOOL_NAME, {"claims": "a, b"})
        assert parse_extracted_claims(response) == ()

    def test_non_string_items_are_skipped(self) -> None:
        response = _response(
            EXTRACT_CLAIMS_TOOL_NAME, {"claims": ["keep", 7, None, "also"]}
        )
        assert parse_extracted_claims(response) == ("keep", "also")

    def test_claim_count_capped(self) -> None:
        claims = [f"claim number {i}" for i in range(MAX_CLAIMS + 5)]
        response = _response(EXTRACT_CLAIMS_TOOL_NAME, {"claims": claims})
        assert len(parse_extracted_claims(response)) == MAX_CLAIMS

    def test_control_characters_are_stripped(self) -> None:
        response = _response(EXTRACT_CLAIMS_TOOL_NAME, {"claims": ["a\x00b\x1bc\x7fd"]})
        assert parse_extracted_claims(response) == ("a b c d",)

    def test_claim_truncated_to_max_chars(self) -> None:
        response = _response(
            EXTRACT_CLAIMS_TOOL_NAME, {"claims": ["x" * (MAX_CLAIM_CHARS + 100)]}
        )
        (claim,) = parse_extracted_claims(response)
        assert len(claim) == MAX_CLAIM_CHARS

    def test_claim_blank_after_strip_is_excluded(self) -> None:
        response = _response(
            EXTRACT_CLAIMS_TOOL_NAME, {"claims": ["\x00 \x1f", "real"]}
        )
        assert parse_extracted_claims(response) == ("real",)

    def test_duplicate_claims_deduped(self) -> None:
        response = _response(
            EXTRACT_CLAIMS_TOOL_NAME, {"claims": ["same", "same", "other"]}
        )
        assert parse_extracted_claims(response) == ("same", "other")

    def test_no_tool_call_returns_empty(self) -> None:
        assert parse_extracted_claims(_response(None, {})) == ()

    def test_other_tool_name_returns_empty(self) -> None:
        response = _response(GROUNDING_VERDICT_TOOL_NAME, {"claims": ["a"]})
        assert parse_extracted_claims(response) == ()


class TestPromptFencing:
    def test_extraction_user_message_fences_deliverable(self) -> None:
        messages = build_extraction_messages("Revenue grew 47%.")
        user = next(m for m in messages if m.role == MessageRole.USER)
        content = user.content
        assert content is not None
        assert f"<{TAG_TASK_DATA}>" in content
        assert "Revenue grew 47%." in content

    def test_extraction_truncates_long_deliverable(self) -> None:
        messages = build_extraction_messages("x" * (MAX_DELIVERABLE_CHARS + 1000))
        user = next(m for m in messages if m.role == MessageRole.USER)
        content = user.content
        assert content is not None
        assert content.count("x") == MAX_DELIVERABLE_CHARS

    def test_entailment_user_message_fences_claim_and_evidence(self) -> None:
        from synthorg.knowledge.enums import SourceType
        from synthorg.knowledge.models import Citation, CodeLocator, KnowledgeHit

        hit = KnowledgeHit(
            chunk_text="Quarterly revenue rose 47 percent.",
            relevance_score=0.9,
            citation=Citation(
                source_id="src-1",
                chunk_id="chunk-1",
                source_type=SourceType.REPO,
                title="Finance",
                uri="repo://finance.md",
                locator=CodeLocator(path="finance.md", line_start=1, line_end=2),
                content_hash="a" * 64,
            ),
        )
        messages = build_entailment_messages("Revenue grew 47%.", (hit,))
        user = next(m for m in messages if m.role == MessageRole.USER)
        content = user.content
        assert content is not None
        assert f"<{TAG_TASK_DATA}>" in content
        assert f"<{TAG_UNTRUSTED_ARTIFACT}>" in content
