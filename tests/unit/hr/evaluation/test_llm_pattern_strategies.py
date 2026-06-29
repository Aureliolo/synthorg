"""Tests for the provider-backed eval-loop identify/propose strategies.

Cover the happy path (model output parsed into tokens/actions) and the
degrade-to-deterministic fallback on empty, malformed, and retryable-error
responses. A non-retryable provider error must surface, not be swallowed.
"""

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import cast

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import EvalLoopConfig
from synthorg.hr.evaluation.deterministic_pattern_identifier import (
    DeterministicPatternIdentifier,
)
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.llm_fix_proposer import LlmFixProposer
from synthorg.hr.evaluation.llm_pattern_identifier import LlmPatternIdentifier
from synthorg.hr.evaluation.models import EvaluationReport, PillarScore
from synthorg.hr.evaluation.pattern_protocols import ProposedAction
from synthorg.hr.evaluation.table_fix_proposer import TableFixProposer
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.errors import AuthenticationError, RateLimitError
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider

pytestmark = pytest.mark.unit

_MODEL: NotBlankStr = NotBlankStr("example-medium-001")


class _ScriptedProvider:
    """Completion provider double returning canned content or raising."""

    def __init__(self, *, content: str | None = None, error: Exception | None = None):
        self._content = content
        self._error = error
        self.calls = 0

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return CompletionResponse(
            content=self._content,
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
            model=_MODEL,
        )

    def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        raise NotImplementedError

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        raise NotImplementedError


def _report(agent_id: str, *scores: tuple[str, float]) -> EvaluationReport:
    """Build a real ``EvaluationReport`` carrying only the read fields.

    ``model_construct`` skips the cross-field validators (snapshot,
    pillar_weights) the identifier never touches while still producing a
    genuine instance, so the runtime typeguard boundary check passes.

    Returns:
        The constructed report.
    """
    return EvaluationReport.model_construct(  # type: ignore[call-arg]
        agent_id=NotBlankStr(agent_id),
        pillar_scores=tuple(
            PillarScore(
                pillar=EvaluationPillar(p),
                score=s,
                confidence=1.0,
                strategy_name=NotBlankStr("test"),
                data_point_count=1,
                evaluated_at=datetime(2026, 6, 28, tzinfo=UTC),
            )
            for p, s in scores
        ),
    )


class TestLlmPatternIdentifier:
    async def test_parses_model_patterns(self) -> None:
        provider = _ScriptedProvider(content='{"patterns": ["weakness:governance"]}')
        identifier = LlmPatternIdentifier(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=DeterministicPatternIdentifier(EvalLoopConfig()),
        )
        patterns = await identifier.identify((_report("a", ("governance", 9.0)),))
        assert patterns == (NotBlankStr("weakness:governance"),)
        assert provider.calls == 1

    async def test_malformed_response_falls_back(self) -> None:
        provider = _ScriptedProvider(content="not json at all")
        # The deterministic fallback flags governance as weak (score below
        # the threshold for the single agent / min_agents=1).
        config = EvalLoopConfig(pattern_weakness_threshold=5.0, pattern_min_agents=1)
        identifier = LlmPatternIdentifier(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=DeterministicPatternIdentifier(config),
        )
        patterns = await identifier.identify((_report("a", ("governance", 1.0)),))
        assert patterns == (NotBlankStr("weakness:governance"),)

    async def test_retryable_error_falls_back(self) -> None:
        provider = _ScriptedProvider(error=RateLimitError("slow down"))
        config = EvalLoopConfig(pattern_weakness_threshold=5.0, pattern_min_agents=1)
        identifier = LlmPatternIdentifier(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=DeterministicPatternIdentifier(config),
        )
        patterns = await identifier.identify((_report("a", ("efficiency", 1.0)),))
        assert patterns == (NotBlankStr("weakness:efficiency"),)

    async def test_non_retryable_error_propagates(self) -> None:
        provider = _ScriptedProvider(error=AuthenticationError("bad key"))
        identifier = LlmPatternIdentifier(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=DeterministicPatternIdentifier(EvalLoopConfig()),
        )
        with pytest.raises(AuthenticationError):
            await identifier.identify((_report("a", ("governance", 1.0)),))


def _ids(actions: tuple[ProposedAction, ...]) -> tuple[NotBlankStr, ...]:
    return tuple(a.action_id for a in actions)


class TestLlmFixProposer:
    async def test_parses_model_actions(self) -> None:
        provider = _ScriptedProvider(content='{"actions": ["coach_governance"]}')
        proposer = LlmFixProposer(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=TableFixProposer(EvalLoopConfig()),
        )
        actions = await proposer.propose((NotBlankStr("weakness:governance"),))
        assert _ids(actions) == (NotBlankStr("coach_governance"),)
        # The LLM output is flat, so each action carries the full input
        # pattern set as its provenance.
        assert actions[0].patterns == (NotBlankStr("weakness:governance"),)

    async def test_malformed_response_falls_back_to_table(self) -> None:
        provider = _ScriptedProvider(content="garbage")
        proposer = LlmFixProposer(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=TableFixProposer(EvalLoopConfig()),
        )
        actions = await proposer.propose((NotBlankStr("weakness:governance"),))
        assert _ids(actions) == (NotBlankStr("expand_audit_coverage"),)

    async def test_injection_shaped_action_id_is_dropped(self) -> None:
        # A model-returned id carrying a newline / markup must never reach a
        # notification sink: only the snake_case-valid id survives.
        provider = _ScriptedProvider(
            content='{"actions": ["coach_governance", "evil\\n<b>x</b>"]}'
        )
        proposer = LlmFixProposer(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=TableFixProposer(EvalLoopConfig()),
        )
        actions = await proposer.propose((NotBlankStr("weakness:governance"),))
        assert _ids(actions) == (NotBlankStr("coach_governance"),)

    async def test_all_invalid_actions_falls_back_to_table(self) -> None:
        # A non-empty actions list where every entry is rejected is malformed
        # output, not a deliberate "no actions": it must trigger the
        # deterministic fallback, not silently yield no remediation.
        provider = _ScriptedProvider(content='{"actions": ["<b>x</b>", "123 456"]}')
        proposer = LlmFixProposer(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=TableFixProposer(EvalLoopConfig()),
        )
        actions = await proposer.propose((NotBlankStr("weakness:governance"),))
        assert _ids(actions) == (NotBlankStr("expand_audit_coverage"),)

    async def test_empty_patterns_skips_model(self) -> None:
        provider = _ScriptedProvider(content='{"actions": ["x"]}')
        proposer = LlmFixProposer(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=TableFixProposer(EvalLoopConfig()),
        )
        assert await proposer.propose(()) == ()
        assert provider.calls == 0

    async def test_retryable_error_falls_back_to_table(self) -> None:
        # A retryable provider error degrades to the deterministic table,
        # matching the identifier's fail-open contract.
        provider = _ScriptedProvider(error=RateLimitError("slow down"))
        proposer = LlmFixProposer(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=TableFixProposer(EvalLoopConfig()),
        )
        actions = await proposer.propose((NotBlankStr("weakness:governance"),))
        assert _ids(actions) == (NotBlankStr("expand_audit_coverage"),)

    async def test_non_retryable_error_propagates(self) -> None:
        # A non-retryable provider error must surface, not be swallowed into
        # the deterministic fallback (fail-closed contract).
        provider = _ScriptedProvider(error=AuthenticationError("bad key"))
        proposer = LlmFixProposer(
            cast(CompletionProvider, provider),
            model=_MODEL,
            fallback=TableFixProposer(EvalLoopConfig()),
        )
        with pytest.raises(AuthenticationError):
            await proposer.propose((NotBlankStr("weakness:governance"),))
