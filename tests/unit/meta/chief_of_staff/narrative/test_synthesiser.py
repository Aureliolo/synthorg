"""Unit tests for the run-narrative synthesiser."""

import json
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.core.completion_enums import FinishReason
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.narrative.constants import FALLBACK_SUMMARY
from synthorg.meta.chief_of_staff.narrative.models import (
    ReducedDecision,
    ReducedRun,
    RunMetric,
)
from synthorg.meta.chief_of_staff.narrative.synthesiser import NarrativeSynthesiser
from synthorg.observability.events.chief_of_staff import COS_NARRATIVE_PROSE_FALLBACK
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.protocol import CompletionProvider
from tests._shared import mock_of
from tests._shared.model_binding import bound_ref, one_connection

pytestmark = pytest.mark.unit


def _run() -> ReducedRun:
    return ReducedRun(
        project_id=NotBlankStr("proj-1"),
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        brief_title=NotBlankStr("Ship checkout"),
        final_status=TaskStatus.COMPLETED,
        metrics=(RunMetric(name="Turns", value="12"),),
        decisions=(
            ReducedDecision(
                title="Adopt ledger",
                outcome="Event-sourced ledger",
                rationale="Auditability wins.",
            ),
        ),
        outcomes=("Final status: completed",),
    )


def _response(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=100, output_tokens=50, cost=0.001),
        model="example-small-001",
    )


def _synth_returning(content: str) -> NarrativeSynthesiser:
    provider = mock_of[CompletionProvider](
        complete=AsyncMock(return_value=_response(content))
    )
    return NarrativeSynthesiser(
        connections=one_connection(provider),
        config=ChiefOfStaffConfig(narrative_model=bound_ref("example-small-001")),
    )


def _synth_raising(exc: type[BaseException] | BaseException) -> NarrativeSynthesiser:
    provider = mock_of[CompletionProvider](complete=AsyncMock(side_effect=exc))
    return NarrativeSynthesiser(
        connections=one_connection(provider),
        config=ChiefOfStaffConfig(narrative_model=bound_ref("example-small-001")),
    )


class TestWriteProse:
    async def test_parses_full_json(self) -> None:
        payload = json.dumps(
            {
                "summary": "The team shipped checkout.",
                "decisions": "One decision shaped the run.",
                "contributions": "Two agents collaborated.",
                "outcomes": "The brief completed.",
            }
        )
        prose = await _synth_returning(payload).write_prose(_run())
        assert prose.summary == "The team shipped checkout."
        assert prose.decisions == "One decision shaped the run."

    async def test_null_optional_sections(self) -> None:
        payload = json.dumps(
            {"summary": "A clean run.", "decisions": None, "contributions": ""}
        )
        prose = await _synth_returning(payload).write_prose(_run())
        assert prose.summary == "A clean run."
        assert prose.decisions is None
        assert prose.contributions is None

    async def test_empty_summary_falls_back(self) -> None:
        payload = json.dumps({"summary": "   "})
        prose = await _synth_returning(payload).write_prose(_run())
        assert prose.summary == FALLBACK_SUMMARY

    async def test_malformed_json_falls_back(self) -> None:
        prose = await _synth_returning("not json at all").write_prose(_run())
        assert prose.summary == FALLBACK_SUMMARY

    async def test_empty_response_falls_back(self) -> None:
        prose = await _synth_returning("").write_prose(_run())
        assert prose.summary == FALLBACK_SUMMARY

    async def test_untrusted_content_is_wrapped(self) -> None:
        # The agent-authored brief title (and the formatted record) must
        # enter the prompt fenced via wrap_untrusted, never raw, so a
        # malicious title cannot inject instructions into the model call.
        provider = mock_of[CompletionProvider](
            complete=AsyncMock(return_value=_response(json.dumps({"summary": "ok"})))
        )
        synth = NarrativeSynthesiser(
            connections=one_connection(provider),
            config=ChiefOfStaffConfig(narrative_model=bound_ref("example-small-001")),
        )
        await synth.write_prose(_run())
        messages = provider.complete.await_args.args[0]
        # Fenced brief title + record ride in the USER message (index 1);
        # the SYSTEM message (index 0) carries the directive.
        assert messages[0].role is MessageRole.SYSTEM
        assert messages[1].role is MessageRole.USER
        content = messages[1].content
        assert wrap_untrusted(TAG_TASK_DATA, "Ship checkout") in content
        # The decision rationale (agent-authored) flows through the fenced
        # record block, so it appears in the prompt too.
        assert "Auditability wins." in content

    async def test_unset_model_falls_back_with_distinct_reason(self) -> None:
        # An unset narrative_model is a config gap, not a provider outage: it
        # degrades to the fallback without ever calling the provider and logs a
        # distinct reason so ops don't read it as an outage.
        provider = mock_of[CompletionProvider](complete=AsyncMock())
        synth = NarrativeSynthesiser(
            connections=one_connection(provider), config=ChiefOfStaffConfig()
        )
        with structlog.testing.capture_logs() as events:
            prose = await synth.write_prose(_run())
        assert prose.summary == FALLBACK_SUMMARY
        provider.complete.assert_not_called()
        assert any(
            e["event"] == COS_NARRATIVE_PROSE_FALLBACK
            and e.get("reason") == "narrative_model_unset"
            for e in events
        )

    async def test_provider_error_falls_back(self) -> None:
        prose = await _synth_raising(RuntimeError("provider down")).write_prose(_run())
        assert prose.summary == FALLBACK_SUMMARY

    async def test_timeout_falls_back(self) -> None:
        prose = await _synth_raising(TimeoutError()).write_prose(_run())
        assert prose.summary == FALLBACK_SUMMARY

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_catastrophic_error_propagates(
        self, exc_cls: type[BaseException]
    ) -> None:
        with pytest.raises(exc_cls):
            await _synth_raising(exc_cls).write_prose(_run())
