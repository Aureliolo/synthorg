"""Advanced tests for hybrid loop: stagnation, tiering, metadata, etc."""

from typing import TYPE_CHECKING, Any

import pytest

from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid.replan_helpers import _parse_replan_decision
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.loop_protocol import TerminationReason, TurnRecord
from synthorg.engine.stagnation.models import (
    StagnationResult,
    StagnationVerdict,
)
from synthorg.providers.models import CompletionResponse

from ._hybrid_loop_helpers import (
    _ctx_with_user_msg,
    _make_invoker,
    _single_step_plan,
    _stop_response,
    _summary_response,
    _tool_use_response,
)

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider


@pytest.mark.unit
class TestHybridLoopStagnation:
    """Stagnation detection integration."""

    async def test_stagnation_within_step_triggers_terminate(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        class TerminateDetector:
            async def check(
                self,
                turns: tuple[TurnRecord, ...],
                *,
                corrections_injected: int = 0,
            ) -> StagnationResult:
                if len(turns) >= 2:
                    return StagnationResult(
                        verdict=StagnationVerdict.TERMINATE,
                        repetition_ratio=1.0,
                    )
                return StagnationResult(
                    verdict=StagnationVerdict.NO_STAGNATION,
                    repetition_ratio=0.0,
                )

            def get_detector_type(self) -> str:
                return "test_terminate"

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _tool_use_response("echo", "tc-1"),  # turn 1
                _tool_use_response("echo", "tc-2"),  # turn 2 -> stagnation
            ]
        )
        invoker = _make_invoker("echo")
        loop = HybridLoop(stagnation_detector=TerminateDetector())

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.STAGNATION

    async def test_stagnation_correction_in_step(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        class CorrectDetector:
            def __init__(self) -> None:
                self._fired = False

            async def check(
                self,
                turns: tuple[TurnRecord, ...],
                *,
                corrections_injected: int = 0,
            ) -> StagnationResult:
                if len(turns) >= 1 and not self._fired:
                    self._fired = True
                    return StagnationResult(
                        verdict=StagnationVerdict.INJECT_PROMPT,
                        corrective_message="Try a different approach.",
                        repetition_ratio=0.6,
                    )
                return StagnationResult(
                    verdict=StagnationVerdict.NO_STAGNATION,
                    repetition_ratio=0.0,
                )

            def get_detector_type(self) -> str:
                return "test_correct"

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _tool_use_response("echo", "tc-1"),  # triggers correction
                _stop_response("Done differently."),  # completes after fix
                _summary_response(),
            ]
        )
        invoker = _make_invoker("echo")
        loop = HybridLoop(stagnation_detector=CorrectDetector())

        result = await loop.execute(
            context=ctx,
            provider=provider,
            tool_invoker=invoker,
        )

        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestHybridLoopModelTiering:
    """Different models for planning vs execution."""

    async def test_different_models_for_phases(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        cfg = HybridLoopConfig(
            planner_model="test-large-001",
            executor_model="test-small-001",
        )
        provider = mock_provider_factory(
            [
                _single_step_plan(),  # planning (large model)
                _stop_response("Done."),  # step (small model)
                _summary_response(),  # summary (large model)
            ]
        )
        loop = HybridLoop(config=cfg)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # Verify model usage
        assert provider.recorded_models[0] == "test-large-001"  # plan
        assert provider.recorded_models[1] == "test-small-001"  # step
        assert provider.recorded_models[2] == "test-large-001"  # summary


@pytest.mark.unit
class TestHybridLoopMetadata:
    """Verify metadata structure."""

    async def test_metadata_structure(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Done."),
                _summary_response(),
            ]
        )
        loop = HybridLoop()

        result = await loop.execute(context=ctx, provider=provider)

        assert result.metadata["loop_type"] == "hybrid"
        assert result.metadata["replans_used"] == 0
        assert isinstance(result.metadata["final_plan"], dict)
        assert "steps" in result.metadata["final_plan"]
        plans = result.metadata["plans"]
        assert isinstance(plans, list)
        assert len(plans) == 1


@pytest.mark.unit
class TestHybridLoopContextImmutability:
    """Original context must not be mutated."""

    async def test_original_context_unchanged(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        ctx = _ctx_with_user_msg(sample_agent_context)
        original_turn_count = ctx.turn_count
        original_conversation_len = len(ctx.conversation)

        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Done."),
                _summary_response(),
            ]
        )
        loop = HybridLoop()

        await loop.execute(context=ctx, provider=provider)

        assert ctx.turn_count == original_turn_count
        assert len(ctx.conversation) == original_conversation_len


@pytest.mark.unit
class TestHybridLoopCheckpointCallback:
    """Checkpoint callback integration."""

    async def test_checkpoint_callback_invoked(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        call_count = 0

        async def checkpoint_cb(_ctx: AgentContext) -> None:
            nonlocal call_count
            call_count += 1

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Done."),
                _summary_response(),
            ]
        )
        loop = HybridLoop(checkpoint_callback=checkpoint_cb)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # Checkpoint called for each LLM turn: plan + step + summary
        assert call_count == 3

    async def test_checkpoint_callback_failure_does_not_propagate(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        async def failing_cb(_ctx: AgentContext) -> None:
            msg = "checkpoint storage unavailable"
            raise OSError(msg)

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Done."),
                _summary_response(),
            ]
        )
        loop = HybridLoop(checkpoint_callback=failing_cb)

        # Should complete despite checkpoint failures
        result = await loop.execute(context=ctx, provider=provider)
        assert result.termination_reason == TerminationReason.COMPLETED


@pytest.mark.unit
class TestHybridLoopCompaction:
    """Compaction callback integration."""

    async def test_compaction_callback_invoked(
        self,
        sample_agent_context: AgentContext,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """When a compaction_callback is provided, it gets called
        during step execution.
        """
        compaction_calls: list[int] = []

        async def compaction_cb(ctx: AgentContext) -> AgentContext | None:
            compaction_calls.append(ctx.turn_count)
            return None  # no compaction performed

        ctx = _ctx_with_user_msg(sample_agent_context)
        provider = mock_provider_factory(
            [
                _single_step_plan(),
                _stop_response("Done."),
                _summary_response(),
            ]
        )
        loop = HybridLoop(compaction_callback=compaction_cb)

        result = await loop.execute(context=ctx, provider=provider)

        assert result.termination_reason == TerminationReason.COMPLETED
        # Compaction is called at least once during step execution
        assert len(compaction_calls) >= 1


@pytest.mark.unit
class TestParseReplanDecision:
    """Unit tests for the module-level _parse_replan_decision helper."""

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            pytest.param('{"summary": "ok", "replan": true}', True, id="json-true"),
            pytest.param('{"summary": "ok", "replan": false}', False, id="json-false"),
            pytest.param(
                '```json\n{"summary": "ok", "replan": true}\n```',
                True,
                id="markdown-fence",
            ),
            pytest.param(
                'I think we need "replan": true based on results.',
                True,
                id="text-heuristic",
            ),
            pytest.param("This is not JSON at all.", False, id="malformed-json"),
            pytest.param("", False, id="empty-string"),
            pytest.param("   ", False, id="whitespace-only"),
            pytest.param("[true]", False, id="non-dict-json"),
            pytest.param('{"summary": "ok"}', False, id="missing-replan-key"),
            pytest.param('{"replan": "true"}', True, id="string-true"),
            pytest.param('{"replan": "false"}', False, id="string-false"),
            pytest.param('{"replan": 1}', False, id="int-treated-as-no-replan"),
        ],
    )
    def test_parse_replan_decision(
        self,
        content: str,
        expected: bool,
    ) -> None:
        assert _parse_replan_decision(content) is expected


@pytest.mark.unit
class TestHybridLoopProviderErrors:
    """Provider error handling."""

    async def test_provider_error_during_planning(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        class FailingProvider:
            async def complete(self, *_args: Any, **_kwargs: Any) -> None:
                msg = "provider unreachable"
                raise ConnectionError(msg)

        ctx = _ctx_with_user_msg(sample_agent_context)
        loop = HybridLoop()

        result = await loop.execute(
            context=ctx,
            provider=FailingProvider(),  # type: ignore[arg-type]
        )
        assert result.termination_reason == TerminationReason.ERROR

    async def test_provider_error_during_step(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        call_count = 0

        class FailingProvider:
            async def complete(self, *_args: Any, **_kwargs: Any) -> CompletionResponse:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return _single_step_plan()
                msg = "provider unreachable"
                raise ConnectionError(msg)

        ctx = _ctx_with_user_msg(sample_agent_context)
        loop = HybridLoop()

        result = await loop.execute(
            context=ctx,
            provider=FailingProvider(),  # type: ignore[arg-type]
        )
        assert result.termination_reason == TerminationReason.ERROR
