"""Unit tests for the run-narrative synthesiser."""

import json
from unittest.mock import AsyncMock

import pytest

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
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.protocol import CompletionProvider
from tests._shared import mock_of

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
    return NarrativeSynthesiser(provider=provider, config=ChiefOfStaffConfig())


def _synth_raising(exc: type[BaseException] | BaseException) -> NarrativeSynthesiser:
    provider = mock_of[CompletionProvider](complete=AsyncMock(side_effect=exc))
    return NarrativeSynthesiser(provider=provider, config=ChiefOfStaffConfig())


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
        synth = NarrativeSynthesiser(provider=provider, config=ChiefOfStaffConfig())
        await synth.write_prose(_run())
        messages = provider.complete.await_args.args[0]
        content = messages[0].content
        assert wrap_untrusted(TAG_TASK_DATA, "Ship checkout") in content
        # The decision rationale (agent-authored) flows through the fenced
        # record block, so it appears in the prompt too.
        assert "Auditability wins." in content

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
