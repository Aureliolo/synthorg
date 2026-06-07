# mypy: disable-error-code="explicit-any"
"""Tests for the procedural memory pipeline (end-to-end)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import FailureCategory, MemoryCategory, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.context_snapshot import AgentContextSnapshot
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.recovery import RecoveryResult
from synthorg.engine.task_execution import TaskExecution
from synthorg.execution.turn import TurnRecord
from synthorg.memory.models import MemoryStoreRequest
from synthorg.memory.procedural.models import (
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
)
from synthorg.memory.procedural.pipeline import (
    _build_payload,
    _format_procedural_content,
    _slugify,
    _try_build_payload,
    materialize_skill_md,
    propose_procedural_memory,
)
from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_MEMORY_ERROR,
    PROCEDURAL_MEMORY_SKILL_MD,
    PROCEDURAL_MEMORY_SKIPPED,
    PROCEDURAL_MEMORY_START,
    PROCEDURAL_MEMORY_STORE_FAILED,
    PROCEDURAL_MEMORY_STORED,
)
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import TokenUsage
from tests._shared import coerce_id, sid


def _make_task(**overrides: Any) -> Task:
    defaults: dict[str, Any] = {
        "id": "task-pipe-001",
        "title": "Implement caching layer",
        "description": "Add Redis caching to the API.",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-001",
        "created_by": "product_manager",
        "assigned_to": "agent-001",
        "status": TaskStatus.ASSIGNED,
    }
    defaults.update(overrides)
    defaults["id"] = coerce_id(defaults["id"])
    return Task(**defaults)


def _make_recovery_result(
    *,
    task: Task | None = None,
    error_message: str = "Provider timeout",
) -> RecoveryResult:
    t = task or _make_task()
    agent_id = str(uuid4())
    exec_id = str(uuid4())
    task_execution = TaskExecution.from_task(t)
    task_execution = task_execution.with_transition(
        TaskStatus.IN_PROGRESS, reason="started"
    )
    task_execution = task_execution.with_transition(
        TaskStatus.FAILED, reason=error_message
    )
    snapshot = AgentContextSnapshot(
        execution_id=exec_id,
        agent_id=agent_id,
        task_id=str(t.id),
        turn_count=3,
        accumulated_cost=TokenUsage(
            input_tokens=500,
            output_tokens=200,
            cost=0.01,
        ),
        task_status=TaskStatus.FAILED,
        started_at=datetime.now(UTC),
        snapshot_at=datetime.now(UTC),
        message_count=6,
    )
    return RecoveryResult(
        task_execution=task_execution,
        strategy_type="fail_reassign",
        context_snapshot=snapshot,
        error_message=error_message,
        failure_category=FailureCategory.TOOL_FAILURE,
        failure_context={},
    )


def _make_execution_result(
    *,
    turn_tool_calls: tuple[tuple[str, ...], ...] = (
        ("code_search",),
        ("run_tests", "write_file"),
    ),
) -> ExecutionResult:
    turns = tuple(
        TurnRecord(
            turn_number=i + 1,
            input_tokens=100,
            output_tokens=50,
            cost=0.001,
            tool_calls_made=tools,
            finish_reason=FinishReason.STOP,
        )
        for i, tools in enumerate(turn_tool_calls)
    )
    task = _make_task()
    ctx = AgentContext.from_identity(_make_identity(), task=task)
    ctx = ctx.with_task_transition(TaskStatus.IN_PROGRESS, reason="started")
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.ERROR,
        turns=turns,
        error_message="Provider timeout",
    )


def _make_identity() -> AgentIdentity:
    from datetime import date

    from synthorg.core.agent import ModelConfig

    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _make_proposal(**overrides: Any) -> ProceduralMemoryProposal:
    defaults: dict[str, Any] = {
        "discovery": "Break large tasks into subtasks when facing timeouts.",
        "condition": "Task fails due to provider timeout after multiple turns.",
        "action": "Decompose the task before retrying.",
        "rationale": "Smaller tasks use less context and are less likely to timeout.",
        "confidence": 0.85,
        "tags": ("timeout", "decomposition"),
    }
    defaults.update(overrides)
    return ProceduralMemoryProposal(**defaults)


# -- _build_payload ---------------------------------------------------


@pytest.mark.unit
class TestBuildPayload:
    def test_extracts_task_fields(self) -> None:
        recovery = _make_recovery_result()
        execution = _make_execution_result()

        payload = _build_payload(execution, recovery)

        assert payload.task_id == sid("task-pipe-001")
        assert payload.task_title == "Implement caching layer"
        assert payload.task_description == "Add Redis caching to the API."
        assert payload.task_type is TaskType.DEVELOPMENT

    def test_extracts_error_and_strategy(self) -> None:
        recovery = _make_recovery_result(error_message="Budget exhausted")
        execution = _make_execution_result()

        payload = _build_payload(execution, recovery)

        assert payload.error_message == "Budget exhausted"
        assert payload.strategy_type == "fail_reassign"
        assert payload.termination_reason == "error"

    def test_flattens_tool_calls_from_turns(self) -> None:
        execution = _make_execution_result(
            turn_tool_calls=(
                ("code_search",),
                ("run_tests", "write_file"),
                ("code_search",),
            ),
        )
        recovery = _make_recovery_result()

        payload = _build_payload(execution, recovery)

        assert payload.tool_calls_made == (
            "code_search",
            "run_tests",
            "write_file",
            "code_search",
        )

    def test_empty_turns_yields_empty_tool_calls(self) -> None:
        execution = _make_execution_result(turn_tool_calls=())
        recovery = _make_recovery_result()

        payload = _build_payload(execution, recovery)
        assert payload.tool_calls_made == ()

    def test_retry_fields(self) -> None:
        recovery = _make_recovery_result()
        execution = _make_execution_result()

        payload = _build_payload(execution, recovery)
        assert payload.retry_count == recovery.task_execution.retry_count
        assert payload.max_retries == recovery.task_execution.task.max_retries
        assert payload.can_reassign == recovery.can_reassign


# -- _format_procedural_content ----------------------------------------


@pytest.mark.unit
class TestFormatProceduralContent:
    def test_three_tier_structure(self) -> None:
        proposal = _make_proposal()
        content = _format_procedural_content(proposal)

        assert "[DISCOVERY]" in content
        assert "[CONDITION]" in content
        assert "[ACTION]" in content
        assert "[RATIONALE]" in content

    def test_contains_proposal_text(self) -> None:
        proposal = _make_proposal(discovery="Use smaller context windows.")
        content = _format_procedural_content(proposal)

        assert "Use smaller context windows." in content


# -- propose_procedural_memory -----------------------------------------


@pytest.mark.unit
class TestProposeProceduralMemory:
    async def test_happy_path_stores_and_returns_id(self) -> None:
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(return_value=_make_proposal())
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-001")

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with structlog.testing.capture_logs() as logs:
            result = await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
            )

        assert result == "mem-001"
        backend.store.assert_awaited_once()
        store_call = backend.store.call_args
        assert store_call[0][0] == "agent-001"
        request: MemoryStoreRequest = store_call[0][1]
        assert request.category is MemoryCategory.PROCEDURAL
        assert "non-inferable" in request.metadata.tags
        assert request.metadata.source == "failure:task-pipe-001"

        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_START in events
        assert PROCEDURAL_MEMORY_STORED in events

    async def test_proposer_returns_none_skips_store(self) -> None:
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(return_value=None)
        backend = AsyncMock(spec=MemoryBackend)

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with structlog.testing.capture_logs() as logs:
            result = await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
            )

        assert result is None
        backend.store.assert_not_awaited()
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_SKIPPED in events

    async def test_store_failure_returns_none(self) -> None:
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(return_value=_make_proposal())
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(side_effect=RuntimeError("store failed"))

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with structlog.testing.capture_logs() as logs:
            result = await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
            )

        assert result is None
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_STORE_FAILED in events

    async def test_tags_include_proposal_tags(self) -> None:
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(
            return_value=_make_proposal(tags=("api_error", "retry")),
        )
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-002")

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        await propose_procedural_memory(
            execution,
            recovery,
            agent_id="agent-001",
            task_id="task-pipe-001",
            proposer=proposer,
            memory_backend=backend,
        )

        request: MemoryStoreRequest = backend.store.call_args[0][1]
        assert "non-inferable" in request.metadata.tags
        assert "api_error" in request.metadata.tags
        assert "retry" in request.metadata.tags

    async def test_confidence_passed_to_metadata(self) -> None:
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(
            return_value=_make_proposal(confidence=0.72),
        )
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-003")

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        await propose_procedural_memory(
            execution,
            recovery,
            agent_id="agent-001",
            task_id="task-pipe-001",
            proposer=proposer,
            memory_backend=backend,
        )

        request: MemoryStoreRequest = backend.store.call_args[0][1]
        assert request.metadata.confidence == 0.72

    async def test_memory_error_propagates_from_store(self) -> None:
        """MemoryError from backend.store is never swallowed."""
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(return_value=_make_proposal())
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(side_effect=MemoryError("oom"))

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with pytest.raises(MemoryError):
            await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
            )

    async def test_payload_build_failure_returns_none(self) -> None:
        """If _build_payload raises, pipeline returns None gracefully."""
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        backend = AsyncMock(spec=MemoryBackend)

        # Use an execution result with no turns and a recovery result
        # whose task has invalid data to trigger a build error.
        execution = _make_execution_result()
        recovery = _make_recovery_result()
        # Monkey-patch to force an error in _build_payload
        object.__setattr__(
            recovery.task_execution.task,
            "title",
            "",
        )

        result = await propose_procedural_memory(
            execution,
            recovery,
            agent_id="agent-001",
            task_id="task-pipe-001",
            proposer=proposer,
            memory_backend=backend,
        )
        assert result is None


# -- _format_procedural_content with execution_steps -------------------


@pytest.mark.unit
class TestFormatWithExecutionSteps:
    def test_includes_execution_section(self) -> None:
        proposal = _make_proposal(
            execution_steps=("Check logs", "Restart service"),
        )
        content = _format_procedural_content(proposal)

        assert "[EXECUTION]" in content
        assert "1. Check logs" in content
        assert "2. Restart service" in content

    def test_omits_execution_when_empty(self) -> None:
        proposal = _make_proposal(execution_steps=())
        content = _format_procedural_content(proposal)

        assert "[EXECUTION]" not in content


# -- materialize_skill_md ----------------------------------------------


@pytest.mark.unit
class TestMaterializeSkillMd:
    def test_writes_skill_md_file(self, tmp_path: Any) -> None:
        proposal = _make_proposal(
            execution_steps=("Step one", "Step two"),
        )
        path = materialize_skill_md(proposal, "task-001", str(tmp_path))

        assert path.exists()
        assert path.name.startswith("SKILL-task-001-")
        assert path.suffix == ".md"

        content = path.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        # yaml.safe_dump produces multiline frontmatter
        assert "trigger:" in content
        assert "confidence: 0.85" in content
        assert "source: failure:task-001" in content
        assert "## Action" in content
        assert "## Execution Steps" in content
        assert "1. Step one" in content

    def test_creates_directory_if_missing(self, tmp_path: Any) -> None:
        nested = tmp_path / "deep" / "nested"
        proposal = _make_proposal()
        path = materialize_skill_md(proposal, "task-002", str(nested))

        assert path.exists()

    def test_omits_execution_section_when_no_steps(self, tmp_path: Any) -> None:
        proposal = _make_proposal(execution_steps=())
        path = materialize_skill_md(proposal, "task-003", str(tmp_path))

        content = path.read_text(encoding="utf-8")
        assert "## Execution Steps" not in content


# -- propose_procedural_memory: config with skill_md_directory -----------


@pytest.mark.unit
class TestProposeWithSkillMdConfig:
    async def test_propose_with_skill_md_config(self, tmp_path: Any) -> None:
        """When config has skill_md_directory, SKILL.md is written."""
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(return_value=_make_proposal())
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-010")
        config = ProceduralMemoryConfig(
            skill_md_directory=str(tmp_path),
        )

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with structlog.testing.capture_logs() as logs:
            result = await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
                config=config,
            )

        assert result == "mem-010"
        # SKILL.md file should exist in the directory
        md_files = list(tmp_path.glob("SKILL-*.md"))
        assert len(md_files) == 1
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_SKILL_MD in events

    async def test_skill_md_write_failure_still_returns_memory_id(
        self,
        tmp_path: Any,
    ) -> None:
        """Filesystem error in materialize does not prevent memory_id return."""
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(return_value=_make_proposal())
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(return_value="mem-011")
        # Use an invalid directory path to trigger a write error
        config = ProceduralMemoryConfig(
            skill_md_directory="/\x00invalid-path",
        )

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with structlog.testing.capture_logs() as logs:
            result = await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
                config=config,
            )

        # Memory ID is still returned despite SKILL.md failure
        assert result == "mem-011"
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_STORED in events


# -- proposer exception handling -----------------------------------------


@pytest.mark.unit
class TestProposerExceptionHandling:
    async def test_proposer_exception_returns_none(self) -> None:
        """RuntimeError from proposer.propose is caught, returns None."""
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(side_effect=RuntimeError("boom"))
        backend = AsyncMock(spec=MemoryBackend)

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with structlog.testing.capture_logs() as logs:
            result = await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
            )

        assert result is None
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_SKIPPED in events

    async def test_memory_error_from_proposer_propagates(self) -> None:
        """MemoryError from proposer.propose is never swallowed."""
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(side_effect=MemoryError("oom"))
        backend = AsyncMock(spec=MemoryBackend)

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with pytest.raises(MemoryError):
            await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
            )


# -- RecursionError propagation ------------------------------------------


@pytest.mark.unit
class TestRecursionErrorPropagation:
    async def test_recursion_error_propagates_from_store(self) -> None:
        """RecursionError from backend.store is never swallowed."""
        proposer = AsyncMock(spec=ProceduralMemoryProposer)
        proposer.propose = AsyncMock(return_value=_make_proposal())
        backend = AsyncMock(spec=MemoryBackend)
        backend.store = AsyncMock(side_effect=RecursionError("stack overflow"))

        execution = _make_execution_result()
        recovery = _make_recovery_result()

        with pytest.raises(RecursionError):
            await propose_procedural_memory(
                execution,
                recovery,
                agent_id="agent-001",
                task_id="task-pipe-001",
                proposer=proposer,
                memory_backend=backend,
            )


# -- _slugify edge cases --------------------------------------------------


@pytest.mark.unit
class TestSlugifyEdgeCases:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", ""),
            ("!!!@@@###", ""),
            ("hello world", "hello-world"),
            ("UPPER-case", "upper-case"),
            ("\u00e9\u00e0\u00fc", ""),  # unicode-only (non-ASCII stripped)
            ("a" * 200, "a" * 80),  # truncated to 80
            ("---leading---", "leading"),
        ],
        ids=[
            "empty",
            "all-special-chars",
            "spaces",
            "uppercase",
            "unicode-only",
            "very-long",
            "leading-trailing-dashes",
        ],
    )
    def test_slugify(self, text: str, expected: str) -> None:
        assert _slugify(text) == expected


# -- _try_build_payload error handling ------------------------------------


@pytest.mark.unit
class TestTryBuildPayload:
    async def test_payload_build_error_logs_and_returns_none(self) -> None:
        """Payload build failure logs PROCEDURAL_MEMORY_ERROR."""
        execution = _make_execution_result()
        recovery = _make_recovery_result()
        # Monkey-patch to force an error in _build_payload
        object.__setattr__(
            recovery.task_execution.task,
            "title",
            "",
        )

        with structlog.testing.capture_logs() as logs:
            result = await _try_build_payload(
                execution,
                recovery,
                "agent-001",
                "task-pipe-001",
            )

        assert result is None
        events = [entry["event"] for entry in logs]
        assert PROCEDURAL_MEMORY_ERROR in events


# -- sanitization applied to error message --------------------------------


@pytest.mark.unit
class TestSanitizationApplied:
    def test_sanitization_applied_to_error_message(self) -> None:
        """Error message with a file path is sanitized in payload."""
        execution = _make_execution_result()
        recovery = _make_recovery_result(
            error_message="Failed at /home/user/secret/file.py",
        )

        payload = _build_payload(execution, recovery)

        # File path should be redacted
        assert "/home/user/secret/file.py" not in payload.error_message
        assert "[REDACTED_PATH]" in payload.error_message
