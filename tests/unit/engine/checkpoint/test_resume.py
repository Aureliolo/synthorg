"""Tests for checkpoint resume helpers."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.engine.checkpoint.models import CheckpointConfig
from synthorg.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
    deserialize_and_reconcile,
    make_loop_with_callback,
)
from synthorg.engine.plan_execute_loop import PlanExecuteLoop
from synthorg.engine.react_loop import ReactLoop
from synthorg.providers.enums import MessageRole

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx_json(
    agent: AgentIdentity,
    task: Task,
    *,
    turn_count: int = 3,
) -> str:
    """Build a serialized AgentContext JSON string."""
    from synthorg.engine.context import AgentContext

    ctx = AgentContext.from_identity(agent, task=task)
    ctx = ctx.model_copy(update={"turn_count": turn_count})
    return ctx.model_dump_json()


def _make_repos() -> tuple[AsyncMock, AsyncMock]:
    """Build mock checkpoint and heartbeat repositories."""
    cp_repo = AsyncMock()
    cp_repo.delete_by_execution = AsyncMock(return_value=2)
    hb_repo = AsyncMock()
    hb_repo.delete = AsyncMock()
    return cp_repo, hb_repo


# ---------------------------------------------------------------------------
# deserialize_and_reconcile
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeserializeAndReconcileSuccess:
    """Happy path — valid JSON produces a reconstituted AgentContext."""

    def test_returns_agent_context(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        ctx_json = _make_ctx_json(
            sample_agent_with_personality,
            sample_task_with_criteria,
        )
        result = deserialize_and_reconcile(
            ctx_json,
            error_message="LLM timeout",
            agent_id="agent-1",
            task_id="task-1",
        )
        from synthorg.engine.context import AgentContext

        assert isinstance(result, AgentContext)

    def test_reconciliation_message_injected(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        ctx_json = _make_ctx_json(
            sample_agent_with_personality,
            sample_task_with_criteria,
            turn_count=5,
        )
        result = deserialize_and_reconcile(
            ctx_json,
            error_message="rate limit exceeded",
            agent_id="agent-1",
            task_id="task-1",
        )
        # Last message should be the reconciliation message
        last_msg = result.conversation[-1]
        assert last_msg.role is MessageRole.SYSTEM
        assert last_msg.content is not None
        assert "turn 5" in last_msg.content
        assert "rate limit exceeded" in last_msg.content
        assert "Review progress and continue" in last_msg.content

    def test_preserves_original_turn_count(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        ctx_json = _make_ctx_json(
            sample_agent_with_personality,
            sample_task_with_criteria,
            turn_count=7,
        )
        result = deserialize_and_reconcile(
            ctx_json,
            error_message="crash",
            agent_id="a",
            task_id="t",
        )
        assert result.turn_count == 7


@pytest.mark.unit
class TestDeserializeAndReconcileError:
    """Error path — invalid JSON raises ValueError."""

    @pytest.mark.parametrize(
        ("label", "checkpoint_json"),
        [
            ("invalid_json", "{not valid json}"),
            ("empty_string", ""),
            ("wrong_schema", '{"not": "an AgentContext"}'),
        ],
    )
    def test_invalid_checkpoint_json_raises(
        self,
        label: str,
        checkpoint_json: str,
    ) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            deserialize_and_reconcile(
                checkpoint_json=checkpoint_json,
                error_message="crash",
                agent_id="agent-1",
                task_id="task-1",
            )


# ---------------------------------------------------------------------------
# make_loop_with_callback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMakeLoopWithCallbackRepos:
    """Loop returned unchanged when repos are None."""

    def test_both_repos_none_returns_original(self) -> None:
        loop = ReactLoop()
        result = make_loop_with_callback(
            loop, None, None, CheckpointConfig(), "agent", "task"
        )
        assert result is loop

    def test_checkpoint_repo_none_returns_original(self) -> None:
        loop = ReactLoop()
        hb_repo = AsyncMock()
        result = make_loop_with_callback(
            loop, None, hb_repo, CheckpointConfig(), "agent", "task"
        )
        assert result is loop

    def test_heartbeat_repo_none_returns_original(self) -> None:
        loop = ReactLoop()
        cp_repo = AsyncMock()
        result = make_loop_with_callback(
            loop, cp_repo, None, CheckpointConfig(), "agent", "task"
        )
        assert result is loop


@pytest.mark.unit
class TestMakeLoopWithCallbackInjection:
    """Loop types get checkpoint callback injected."""

    def test_react_loop_gets_callback(self) -> None:
        cp_repo, hb_repo = _make_repos()
        original = ReactLoop()
        result = make_loop_with_callback(
            original,
            cp_repo,
            hb_repo,
            CheckpointConfig(),
            "agent-1",
            "task-1",
        )
        assert isinstance(result, ReactLoop)
        assert result is not original

    def test_plan_execute_loop_gets_callback(self) -> None:
        cp_repo, hb_repo = _make_repos()
        original = PlanExecuteLoop()
        result = make_loop_with_callback(
            original,
            cp_repo,
            hb_repo,
            CheckpointConfig(),
            "agent-1",
            "task-1",
        )
        assert isinstance(result, PlanExecuteLoop)
        assert result is not original

    def test_plan_execute_loop_preserves_config(self) -> None:
        from synthorg.engine.plan_models import PlanExecuteConfig

        cp_repo, hb_repo = _make_repos()
        config = PlanExecuteConfig(max_replans=5)
        original = PlanExecuteLoop(config=config)
        result = make_loop_with_callback(
            original,
            cp_repo,
            hb_repo,
            CheckpointConfig(),
            "agent-1",
            "task-1",
        )
        assert isinstance(result, PlanExecuteLoop)
        assert result.config is config

    def test_unsupported_loop_type_returns_original(self) -> None:
        cp_repo, hb_repo = _make_repos()

        class CustomLoop:
            """Custom loop not supported by make_loop_with_callback."""

        original = CustomLoop()
        result = make_loop_with_callback(
            original,  # type: ignore[arg-type]
            cp_repo,
            hb_repo,
            CheckpointConfig(),
            "agent-1",
            "task-1",
        )
        assert result is original  # type: ignore[comparison-overlap]


# ---------------------------------------------------------------------------
# cleanup_checkpoint_artifacts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupCheckpointArtifactsSuccess:
    """Happy path — cleanup deletes checkpoints and heartbeat."""

    async def test_deletes_both(self) -> None:
        cp_repo, hb_repo = _make_repos()
        await cleanup_checkpoint_artifacts(cp_repo, hb_repo, "exec-1")
        cp_repo.delete_by_execution.assert_awaited_once_with("exec-1")
        hb_repo.delete.assert_awaited_once_with("exec-1")

    async def test_both_repos_none_is_noop(self) -> None:
        await cleanup_checkpoint_artifacts(None, None, "exec-1")
        # Should not raise

    async def test_checkpoint_repo_none_only_deletes_heartbeat(self) -> None:
        hb_repo = AsyncMock()
        await cleanup_checkpoint_artifacts(None, hb_repo, "exec-1")
        hb_repo.delete.assert_awaited_once_with("exec-1")

    async def test_heartbeat_repo_none_only_deletes_checkpoints(self) -> None:
        cp_repo = AsyncMock()
        cp_repo.delete_by_execution = AsyncMock(return_value=3)
        await cleanup_checkpoint_artifacts(cp_repo, None, "exec-1")
        cp_repo.delete_by_execution.assert_awaited_once_with("exec-1")


@pytest.mark.unit
class TestCleanupCheckpointArtifactsErrors:
    """Error paths — errors are logged but not propagated."""

    async def test_checkpoint_delete_error_swallowed(self) -> None:
        cp_repo = AsyncMock()
        cp_repo.delete_by_execution = AsyncMock(
            side_effect=RuntimeError("DB error"),
        )
        hb_repo = AsyncMock()
        # Should not raise
        await cleanup_checkpoint_artifacts(cp_repo, hb_repo, "exec-1")
        # Heartbeat delete should still be called
        hb_repo.delete.assert_awaited_once()

    async def test_heartbeat_delete_error_swallowed(self) -> None:
        cp_repo = AsyncMock()
        cp_repo.delete_by_execution = AsyncMock(return_value=1)
        hb_repo = AsyncMock()
        hb_repo.delete = AsyncMock(side_effect=RuntimeError("HB error"))
        # Should not raise
        await cleanup_checkpoint_artifacts(cp_repo, hb_repo, "exec-1")
        # Checkpoint delete should have succeeded
        cp_repo.delete_by_execution.assert_awaited_once()

    async def test_both_errors_swallowed(self) -> None:
        cp_repo = AsyncMock()
        cp_repo.delete_by_execution = AsyncMock(
            side_effect=RuntimeError("CP error"),
        )
        hb_repo = AsyncMock()
        hb_repo.delete = AsyncMock(side_effect=RuntimeError("HB error"))
        # Should not raise even when both fail
        await cleanup_checkpoint_artifacts(cp_repo, hb_repo, "exec-1")
