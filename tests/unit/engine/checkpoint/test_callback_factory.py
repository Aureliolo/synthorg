"""Tests for make_checkpoint_callback factory."""

from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import SeniorityLevel, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.checkpoint.callback_factory import make_checkpoint_callback
from synthorg.engine.checkpoint.models import CheckpointConfig
from synthorg.engine.context import AgentContext

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent() -> AgentIdentity:
    """Build a test agent identity."""
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.MID,
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(primary=("python",)),
    )


def _make_task(agent: AgentIdentity) -> Task:
    """Build a test task."""
    return Task(
        id="task-cb-001",
        title="Callback test",
        description="Test task for callback factory.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="manager",
        assigned_to=str(agent.id),
        status=TaskStatus.ASSIGNED,
    )


def _make_ctx_at_turn(
    agent: AgentIdentity,
    task: Task,
    turn: int,
) -> AgentContext:
    """Build an AgentContext at a given turn count."""
    ctx = AgentContext.from_identity(agent, task=task)
    # Use model_copy to set the desired turn count
    return ctx.model_copy(update={"turn_count": turn})


def _make_repos() -> tuple[AsyncMock, AsyncMock]:
    """Build mock checkpoint and heartbeat repositories."""
    checkpoint_repo = AsyncMock()
    checkpoint_repo.save = AsyncMock()
    heartbeat_repo = AsyncMock()
    heartbeat_repo.save = AsyncMock()
    return checkpoint_repo, heartbeat_repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckpointCallbackBoundaryTurns:
    """Checkpoint is saved on boundary turns based on persist_every_n_turns."""

    async def test_saves_on_every_turn_default(self) -> None:
        """persist_every_n_turns=1 saves every turn with correct fields."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx1 = _make_ctx_at_turn(agent, task, 1)
        await callback(ctx1)
        assert cp_repo.save.await_count == 1

        # Verify checkpoint model content
        saved_cp = cp_repo.save.call_args_list[0][0][0]
        assert saved_cp.agent_id == str(agent.id)
        assert saved_cp.task_id == task.id
        assert saved_cp.turn_number == 1
        assert saved_cp.execution_id == ctx1.execution_id

        # Verify heartbeat model content
        saved_hb = hb_repo.save.call_args_list[0][0][0]
        assert saved_hb.agent_id == str(agent.id)
        assert saved_hb.task_id == task.id
        assert saved_hb.execution_id == ctx1.execution_id

        ctx2 = _make_ctx_at_turn(agent, task, 2)
        await callback(ctx2)
        assert cp_repo.save.await_count == 2

    async def test_skips_non_boundary_turns(self) -> None:
        """persist_every_n_turns=2 skips odd turns."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        config = CheckpointConfig(persist_every_n_turns=2)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        # Turn 1: 1 % 2 != 0 → skip
        ctx1 = _make_ctx_at_turn(agent, task, 1)
        await callback(ctx1)
        assert cp_repo.save.await_count == 0

        # Turn 2: 2 % 2 == 0 → save
        ctx2 = _make_ctx_at_turn(agent, task, 2)
        await callback(ctx2)
        assert cp_repo.save.await_count == 1

        # Turn 3: 3 % 2 != 0 → skip
        ctx3 = _make_ctx_at_turn(agent, task, 3)
        await callback(ctx3)
        assert cp_repo.save.await_count == 1

    @pytest.mark.parametrize(
        ("persist_every_n", "turn", "should_save"),
        [
            (1, 0, False),  # Turn 0 always skipped
            (1, 1, True),
            (1, 5, True),
            (2, 0, False),  # Turn 0 always skipped
            (2, 1, False),
            (2, 2, True),
            (2, 4, True),
            (3, 1, False),
            (3, 2, False),
            (3, 3, True),
            (3, 6, True),
            (5, 3, False),
            (5, 5, True),
        ],
    )
    async def test_persist_boundary_parametrized(
        self,
        persist_every_n: int,
        turn: int,
        should_save: bool,
    ) -> None:
        """Verify boundary turn detection with various configurations."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        config = CheckpointConfig(persist_every_n_turns=persist_every_n)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, turn)
        await callback(ctx)

        expected_count = 1 if should_save else 0
        assert cp_repo.save.await_count == expected_count


@pytest.mark.unit
class TestCheckpointCallbackHeartbeat:
    """Heartbeat is updated alongside checkpoint."""

    async def test_heartbeat_updated_on_save(self) -> None:
        """Heartbeat repo is called when checkpoint is saved."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, 1)
        await callback(ctx)

        hb_repo.save.assert_awaited_once()

    async def test_heartbeat_not_called_on_skip(self) -> None:
        """Heartbeat not updated when turn is skipped."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        config = CheckpointConfig(persist_every_n_turns=2)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        # Turn 1 is skipped with persist_every_n_turns=2
        ctx = _make_ctx_at_turn(agent, task, 1)
        await callback(ctx)

        hb_repo.save.assert_not_awaited()


@pytest.mark.unit
class TestCheckpointCallbackErrorHandling:
    """Errors are swallowed except MemoryError and RecursionError."""

    async def test_checkpoint_repo_error_swallowed(self) -> None:
        """Checkpoint repo error is logged but not propagated."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        cp_repo.save = AsyncMock(side_effect=RuntimeError("DB write failed"))
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, 1)
        # Should not raise
        await callback(ctx)

    async def test_heartbeat_repo_error_swallowed(self) -> None:
        """Heartbeat repo error is logged but not propagated."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        hb_repo.save = AsyncMock(side_effect=RuntimeError("Heartbeat write failed"))
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, 1)
        # Should not raise
        await callback(ctx)

    async def test_memory_error_not_swallowed_from_checkpoint(self) -> None:
        """MemoryError from checkpoint save propagates."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        cp_repo.save = AsyncMock(side_effect=MemoryError)
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, 1)
        with pytest.raises(MemoryError):
            await callback(ctx)

    async def test_recursion_error_not_swallowed_from_checkpoint(self) -> None:
        """RecursionError from checkpoint save propagates."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        cp_repo.save = AsyncMock(side_effect=RecursionError)
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, 1)
        with pytest.raises(RecursionError):
            await callback(ctx)

    async def test_memory_error_not_swallowed_from_heartbeat(self) -> None:
        """MemoryError from heartbeat save propagates."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        hb_repo.save = AsyncMock(side_effect=MemoryError)
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, 1)
        with pytest.raises(MemoryError):
            await callback(ctx)

    async def test_recursion_error_not_swallowed_from_heartbeat(self) -> None:
        """RecursionError from heartbeat save propagates."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        hb_repo.save = AsyncMock(side_effect=RecursionError)
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, 1)
        with pytest.raises(RecursionError):
            await callback(ctx)

    async def test_checkpoint_error_skips_heartbeat(self) -> None:
        """When checkpoint save fails, heartbeat is skipped to avoid limbo state."""
        agent = _make_agent()
        task = _make_task(agent)
        cp_repo, hb_repo = _make_repos()
        cp_repo.save = AsyncMock(side_effect=RuntimeError("checkpoint failed"))
        config = CheckpointConfig(persist_every_n_turns=1)

        callback = make_checkpoint_callback(
            checkpoint_repo=cp_repo,
            heartbeat_repo=hb_repo,
            config=config,
            agent_id=str(agent.id),
            task_id=task.id,
        )

        ctx = _make_ctx_at_turn(agent, task, 1)
        await callback(ctx)

        # Checkpoint save was attempted
        cp_repo.save.assert_awaited_once()
        # Heartbeat should NOT be called when checkpoint failed
        hb_repo.save.assert_not_awaited()
