"""Tests for post-execution memory hooks (standalone functions)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.post_execution.memory_hooks import (
    try_capture_distillation,
    try_procedural_memory,
)
from synthorg.execution.turn import TurnRecord
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability.events.procedural_memory import PROCEDURAL_MEMORY_ERROR
from synthorg.settings.resolver import ConfigResolver
from tests._shared import as_uuid, mock_of

_AGENT_UUID = as_uuid("memory-hooks-agent")


def _make_identity() -> AgentIdentity:
    return AgentIdentity(
        id=_AGENT_UUID,
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
        hiring_date=date(2026, 1, 1),
    )


def _make_task() -> Task:
    return Task(
        id=as_uuid("task-hook-001"),
        title="Hook test task",
        description="A task for testing hooks.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="product_manager",
        assigned_to=str(_AGENT_UUID),
        status=TaskStatus.ASSIGNED,
    )


def _make_completed_result() -> ExecutionResult:
    identity = _make_identity()
    task = _make_task()
    ctx = AgentContext.from_identity(identity, task=task)
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
    turns = (
        TurnRecord(
            turn_number=1,
            input_tokens=100,
            output_tokens=50,
            cost=0.001,
            tool_calls_made=(),
            finish_reason=FinishReason.STOP,
        ),
    )
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
        turns=turns,
    )


def _make_error_result() -> ExecutionResult:
    identity = _make_identity()
    task = _make_task()
    ctx = AgentContext.from_identity(identity, task=task)
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.ERROR,
        turns=(),
        error_message="provider timeout",
    )


def _make_recovery_result() -> MagicMock:
    """Create a mock recovery result (truthy, non-None)."""
    return MagicMock()


# ── try_capture_distillation ──────────────────────────────────────


@pytest.mark.unit
class TestTryCaptureDistillation:
    async def test_capture_called_when_enabled(self) -> None:
        """Stores a distillation reflecting the completed run.

        The error-path sibling asserts the stored content, so the happy
        path holds to the same bar rather than only that store was
        reached: a capture that persisted the wrong outcome would pass a
        bare await-count check.
        """
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="dist-1")
        result = _make_completed_result()

        await try_capture_distillation(
            result,
            str(_AGENT_UUID),
            "task-hook-001",
            distillation_capture_enabled=True,
            memory_backend=backend,
        )

        backend.store.assert_awaited_once()
        store_request = backend.store.call_args.args[1]
        assert "Task completed successfully" in store_request.content

    async def test_skipped_when_disabled(self) -> None:
        """No-op when distillation capture is disabled."""
        backend = AsyncMock(spec=MemoryBackend)
        result = _make_completed_result()

        await try_capture_distillation(
            result,
            str(_AGENT_UUID),
            "task-hook-001",
            distillation_capture_enabled=False,
            memory_backend=backend,
        )

        backend.store.assert_not_awaited()

    async def test_skipped_when_no_backend(self) -> None:
        """No-op when memory backend is None."""
        result = _make_completed_result()

        # Should not raise.
        await try_capture_distillation(
            result,
            str(_AGENT_UUID),
            "task-hook-001",
            distillation_capture_enabled=True,
            memory_backend=None,
        )

    async def test_captures_error_termination(self) -> None:
        """Distillation captures failed runs too."""
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="dist-err")
        result = _make_error_result()

        await try_capture_distillation(
            result,
            str(_AGENT_UUID),
            "task-hook-001",
            distillation_capture_enabled=True,
            memory_backend=backend,
        )

        backend.store.assert_awaited_once()
        store_request = backend.store.call_args.args[1]
        assert "Task failed" in store_request.content
        assert "provider timeout" in store_request.content


# ── try_procedural_memory ─────────────────────────────────────────


@pytest.mark.unit
class TestTryProceduralMemory:
    async def test_skipped_when_no_proposer(self) -> None:
        """No-op when procedural proposer is None."""
        result = _make_error_result()
        recovery = _make_recovery_result()
        backend = AsyncMock(spec=MemoryBackend)

        await try_procedural_memory(
            result,
            recovery,
            str(_AGENT_UUID),
            "task-hook-001",
            procedural_proposer=None,
            memory_backend=backend,
        )

        backend.store.assert_not_awaited()

    async def test_skipped_when_no_recovery(self) -> None:
        """No-op when recovery_result is None."""
        result = _make_completed_result()
        proposer = AsyncMock()
        backend = AsyncMock(spec=MemoryBackend)

        await try_procedural_memory(
            result,
            None,
            str(_AGENT_UUID),
            "task-hook-001",
            procedural_proposer=proposer,
            memory_backend=backend,
        )

        backend.store.assert_not_awaited()

    async def test_pipeline_called_when_proposer_and_recovery_exist(
        self,
    ) -> None:
        """Delegates to propose_procedural_memory when both are present."""
        result = _make_error_result()
        recovery = _make_recovery_result()
        proposer = AsyncMock()
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-001")

        with patch(
            "synthorg.memory.procedural.pipeline.propose_procedural_memory",
            new_callable=AsyncMock,
        ) as mock_propose:
            await try_procedural_memory(
                result,
                recovery,
                str(_AGENT_UUID),
                "task-hook-001",
                procedural_proposer=proposer,
                memory_backend=backend,
            )

            mock_propose.assert_awaited_once()
            call_kwargs = mock_propose.call_args
            assert call_kwargs[1]["proposer"] is proposer
            assert call_kwargs[1]["memory_backend"] is backend

    async def test_exception_swallowed_and_logged(self) -> None:
        """Non-system exceptions are logged, not raised."""
        result = _make_error_result()
        recovery = _make_recovery_result()
        proposer = AsyncMock()
        backend = AsyncMock(spec=MemoryBackend)

        with (
            patch(
                "synthorg.memory.procedural.pipeline.propose_procedural_memory",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            structlog.testing.capture_logs() as cap,
        ):
            # Should not raise.
            await try_procedural_memory(
                result,
                recovery,
                str(_AGENT_UUID),
                "task-hook-001",
                procedural_proposer=proposer,
                memory_backend=backend,
            )

        # Swallowed, but not silently: a best-effort hook that eats an
        # error without a trace is how the original bug hid.
        assert any(e.get("event") == PROCEDURAL_MEMORY_ERROR for e in cap)

    async def test_memory_error_propagates(self) -> None:
        """MemoryError is never swallowed."""
        result = _make_error_result()
        recovery = _make_recovery_result()
        proposer = AsyncMock()
        backend = AsyncMock(spec=MemoryBackend)

        with (
            patch(
                "synthorg.memory.procedural.pipeline.propose_procedural_memory",
                new_callable=AsyncMock,
                side_effect=MemoryError("out of memory"),
            ),
            pytest.raises(MemoryError, match="out of memory"),
        ):
            await try_procedural_memory(
                result,
                recovery,
                str(_AGENT_UUID),
                "task-hook-001",
                procedural_proposer=proposer,
                memory_backend=backend,
            )


# ── live settings gate ────────────────────────────────────────────


def _resolver(*, enabled: bool, min_confidence: float) -> ConfigResolver:
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_bool=AsyncMock(return_value=enabled),
        get_float=AsyncMock(return_value=min_confidence),
    )
    return resolver


@pytest.mark.unit
class TestProceduralLiveSettingsGate:
    """``memory.procedural_enabled`` takes effect on the next capture."""

    async def test_disabled_setting_short_circuits_the_pipeline(self) -> None:
        """A capture makes no LLM call while the switch is off."""
        with patch(
            "synthorg.memory.procedural.pipeline.propose_procedural_memory",
            new_callable=AsyncMock,
        ) as mock_propose:
            await try_procedural_memory(
                _make_error_result(),
                _make_recovery_result(),
                str(_AGENT_UUID),
                "task-hook-001",
                procedural_proposer=AsyncMock(spec=ProceduralMemoryProposer),
                memory_backend=AsyncMock(spec=MemoryBackend),
                procedural_memory_config=ProceduralMemoryConfig(),
                config_resolver=_resolver(enabled=False, min_confidence=0.5),
            )

        mock_propose.assert_not_awaited()

    async def test_disabled_boot_config_short_circuits_without_a_resolver(
        self,
    ) -> None:
        """With no settings wired, the boot config's own switch decides."""
        with patch(
            "synthorg.memory.procedural.pipeline.propose_procedural_memory",
            new_callable=AsyncMock,
        ) as mock_propose:
            await try_procedural_memory(
                _make_error_result(),
                _make_recovery_result(),
                str(_AGENT_UUID),
                "task-hook-001",
                procedural_proposer=AsyncMock(spec=ProceduralMemoryProposer),
                memory_backend=AsyncMock(spec=MemoryBackend),
                procedural_memory_config=ProceduralMemoryConfig(enabled=False),
            )

        mock_propose.assert_not_awaited()

    async def test_live_min_confidence_overrides_the_boot_value(self) -> None:
        """The quality floor is re-read per capture, not frozen at boot."""
        with patch(
            "synthorg.memory.procedural.pipeline.propose_procedural_memory",
            new_callable=AsyncMock,
        ) as mock_propose:
            await try_procedural_memory(
                _make_error_result(),
                _make_recovery_result(),
                str(_AGENT_UUID),
                "task-hook-001",
                procedural_proposer=AsyncMock(spec=ProceduralMemoryProposer),
                memory_backend=AsyncMock(spec=MemoryBackend),
                procedural_memory_config=ProceduralMemoryConfig(min_confidence=0.5),
                config_resolver=_resolver(enabled=True, min_confidence=0.9),
            )

        config = mock_propose.call_args[1]["config"]
        assert config is not None
        assert config.min_confidence == pytest.approx(0.9)

    async def test_resolver_failure_falls_back_to_the_boot_config(self) -> None:
        """A settings read failure never silently flips the switch."""
        resolver = mock_of[ConfigResolver](
            get_bool=AsyncMock(side_effect=RuntimeError("settings down")),
            get_float=AsyncMock(return_value=0.9),
        )
        boot_config = ProceduralMemoryConfig(min_confidence=0.4)

        with patch(
            "synthorg.memory.procedural.pipeline.propose_procedural_memory",
            new_callable=AsyncMock,
        ) as mock_propose:
            await try_procedural_memory(
                _make_error_result(),
                _make_recovery_result(),
                str(_AGENT_UUID),
                "task-hook-001",
                procedural_proposer=AsyncMock(spec=ProceduralMemoryProposer),
                memory_backend=AsyncMock(spec=MemoryBackend),
                procedural_memory_config=boot_config,
                config_resolver=resolver,
            )

        assert mock_propose.call_args[1]["config"] is boot_config
