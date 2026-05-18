"""E2E acceptance: a recorded agent run replays byte-identically.

This is the #1984 acceptance proof under the live engine harness: a
multi-turn single-agent task is run once through a real driver in
**record** mode (tool-use turn + completion turn), then the identical
task is re-run in **replay** mode with a raising spy as the inner
driver. The replay must reproduce the run byte-for-byte while making
**zero** real provider calls.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.core.enums import TaskStatus
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.run_result import AgentRunResult
from synthorg.providers.base import BaseCompletionProvider
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.cassette.mode import CassetteMode
from synthorg.providers.cassette.provider import CassetteCompletionProvider
from synthorg.providers.cassette.redaction import PatternRedactor
from synthorg.providers.cassette.store import CassetteSession
from synthorg.providers.drivers.scripted import (
    ScriptedDriver,
    SequencedResponseStrategy,
)
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry

from .conftest import make_e2e_identity, make_e2e_task

pytestmark = pytest.mark.e2e

_PROVIDER = "cassette-e2e-provider"


def _scripted_run() -> tuple[CompletionResponse, ...]:
    """A deterministic two-turn run: write a file, then complete."""
    return (
        CompletionResponse(
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="write_file",
                    arguments={
                        "path": "output.txt",
                        "content": "Hello cassette",
                    },
                ),
            ),
            finish_reason=FinishReason.TOOL_USE,
            usage=TokenUsage(input_tokens=40, output_tokens=12, cost=0.004),
            model="m",
        ),
        CompletionResponse(
            content="File created successfully.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=60, output_tokens=20, cost=0.006),
            model="m",
        ),
    )


class _RaisingInner(BaseCompletionProvider):
    """Inner driver that explodes if reached -- proves zero real calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def _do_complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        del messages, model, tools, config
        self.calls += 1
        msg = "real provider called during replay"
        raise AssertionError(msg)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        del messages, model, tools, config
        self.calls += 1
        msg = "real provider called during replay"
        raise AssertionError(msg)

    async def _do_get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        del model
        self.calls += 1
        msg = "real provider called during replay"
        raise AssertionError(msg)


async def _run_task(
    provider: BaseCompletionProvider, workspace: Path
) -> AgentRunResult:
    """Drive one single-agent task to completion through the engine."""
    registry = ToolRegistry([WriteFileTool(workspace_root=workspace)])
    identity = make_e2e_identity()
    task = make_e2e_task(
        identity=identity,
        title="Create output file",
        description="Write 'Hello cassette' to output.txt.",
    )
    engine = AgentEngine(
        provider=provider,
        tool_registry=registry,
        cost_tracker=CostTracker(),
    )
    return await engine.run(identity=identity, task=task, max_turns=5)


class TestRecordThenReplayByteIdentical:
    """The headline #1984 acceptance test."""

    async def test_recorded_run_replays_byte_identically(self, tmp_path: Path) -> None:
        cassette = tmp_path / "agent_run.json"

        # -- Record: real (scripted) driver drives the engine. --------
        rec_ws = tmp_path / "rec_ws"
        rec_ws.mkdir()
        rec_session = CassetteSession(
            mode=CassetteMode.RECORD,
            path=cassette,
            redactor=PatternRedactor(),
        )
        rec_provider = CassetteCompletionProvider(
            inner=ScriptedDriver(
                _PROVIDER,
                strategy=SequencedResponseStrategy(_scripted_run()),
            ),
            session=rec_session,
            provider_name=_PROVIDER,
        )
        recorded = await _run_task(rec_provider, rec_ws)
        rec_session.flush()

        assert recorded.is_success is True
        assert recorded.termination_reason == TerminationReason.COMPLETED
        assert (rec_ws / "output.txt").read_text(encoding="utf-8") == "Hello cassette"

        # -- Replay: NO real driver; a raising spy proves zero calls. -
        rep_ws = tmp_path / "rep_ws"
        rep_ws.mkdir()
        spy = _RaisingInner()
        rep_provider = CassetteCompletionProvider(
            inner=spy,
            session=CassetteSession(
                mode=CassetteMode.REPLAY,
                path=cassette,
                redactor=PatternRedactor(),
            ),
            provider_name=_PROVIDER,
        )
        replayed = await _run_task(rep_provider, rep_ws)

        # Zero real provider calls during the entire replayed run.
        assert spy.calls == 0

        # Byte-identical outcome.
        assert replayed.is_success == recorded.is_success
        assert replayed.termination_reason == recorded.termination_reason
        assert replayed.total_turns == recorded.total_turns
        assert replayed.total_cost == recorded.total_cost
        assert replayed.completion_summary == recorded.completion_summary

        rec_conv = recorded.execution_result.context.conversation
        rep_conv = replayed.execution_result.context.conversation
        assert [m.model_dump(mode="json") for m in rep_conv] == [
            m.model_dump(mode="json") for m in rec_conv
        ]

        rec_te = recorded.execution_result.context.task_execution
        rep_te = replayed.execution_result.context.task_execution
        assert rec_te is not None
        assert rep_te is not None
        assert rep_te.status == rec_te.status == TaskStatus.IN_REVIEW

        # The replay reproduced the on-disk artefact too.
        assert (rep_ws / "output.txt").read_text(encoding="utf-8") == "Hello cassette"

    async def test_second_replay_is_stable(self, tmp_path: Path) -> None:
        """Replaying twice yields identical results (no cursor leak)."""
        cassette = tmp_path / "run.json"
        rec_ws = tmp_path / "r0"
        rec_ws.mkdir()
        rec_session = CassetteSession(
            mode=CassetteMode.RECORD,
            path=cassette,
            redactor=PatternRedactor(),
        )
        await _run_task(
            CassetteCompletionProvider(
                inner=ScriptedDriver(
                    _PROVIDER,
                    strategy=SequencedResponseStrategy(_scripted_run()),
                ),
                session=rec_session,
                provider_name=_PROVIDER,
            ),
            rec_ws,
        )
        rec_session.flush()

        summaries: list[str | None] = []
        for i in range(2):
            ws = tmp_path / f"replay{i}"
            ws.mkdir()
            result = await _run_task(
                CassetteCompletionProvider(
                    inner=None,
                    session=CassetteSession(
                        mode=CassetteMode.REPLAY,
                        path=cassette,
                        redactor=PatternRedactor(),
                    ),
                    provider_name=_PROVIDER,
                ),
                ws,
            )
            summaries.append(result.completion_summary)
        assert summaries[0] == summaries[1]
