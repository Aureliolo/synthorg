"""E2E tests: single agent completes real tasks end-to-end.

Validates the core MVP hypothesis -- a single agent can complete a real
task through the full execution pipeline (engine, execution loop, real tools,
cost tracking, task lifecycle).
"""

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from synthorg.budget.tracker import CostTracker
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.agent import ModelConfig, ToolPermissions
from synthorg.core.enums import TaskStatus, ToolAccessLevel
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.providers.drivers.litellm_driver import LiteLLMDriver
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ToolCall
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry

from .conftest import (
    ScriptedProvider,
    make_e2e_identity,
    make_e2e_task,
    make_text_response,
    make_tool_call_response,
)

pytestmark = pytest.mark.e2e


class TestFileToolAgent:
    """Agent creates a file using real file system tools."""

    async def test_agent_creates_file_with_real_tools(
        self, e2e_workspace: Path
    ) -> None:
        """Agent writes a file to disk, then completes with a summary."""
        write_tool = WriteFileTool(workspace_root=e2e_workspace)
        registry = ToolRegistry([write_tool])
        cost_tracker = CostTracker()

        identity = make_e2e_identity()
        task = make_e2e_task(
            identity=identity,
            title="Create output file",
            description="Write 'Hello from agent' to output.txt.",
        )

        provider = ScriptedProvider(
            [
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id="call-001",
                            name="write_file",
                            arguments={
                                "path": "output.txt",
                                "content": "Hello from agent",
                            },
                        ),
                    ),
                ),
                make_text_response("File created successfully."),
            ]
        )

        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
            cost_tracker=cost_tracker,
        )
        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=5,
        )

        # Successful completion
        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_turns == 2

        # File exists on disk with correct content
        output_file = e2e_workspace / "output.txt"
        assert output_file.exists()
        assert output_file.read_text(encoding="utf-8") == "Hello from agent"

        # Tool result in conversation contains success message
        conversation = result.execution_result.context.conversation
        tool_msgs = [m for m in conversation if m.role == MessageRole.TOOL]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_result is not None
        assert tool_msgs[0].tool_result.is_error is False
        assert "Created output.txt" in tool_msgs[0].tool_result.content

        # Task lifecycle: ASSIGNED -> IN_PROGRESS -> IN_REVIEW (review gate)
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.IN_REVIEW
        assert len(task_execution.transition_log) == 2
        assert task_execution.transition_log[0].to_status == TaskStatus.IN_PROGRESS
        assert task_execution.transition_log[1].to_status == TaskStatus.IN_REVIEW

        # Cost tracking matches result
        total_cost = await cost_tracker.get_total_cost()
        assert total_cost == pytest.approx(result.total_cost)
        assert await cost_tracker.get_record_count() == 2

        # Completion summary is non-empty
        assert result.completion_summary is not None
        assert len(result.completion_summary) > 0

        # IDs and duration
        assert result.agent_id == str(identity.id)
        assert result.task_id == str(task.id)
        assert result.duration_seconds > 0


class TestTextOnlyAgent:
    """Agent answers a question without using any tools."""

    async def test_text_only_completion(self) -> None:
        """Agent produces a text answer in a single turn."""
        cost_tracker = CostTracker()
        identity = make_e2e_identity()
        task = make_e2e_task(
            identity=identity,
            title="Answer a question",
            description="What is the meaning of life?",
        )

        provider = ScriptedProvider(
            [
                make_text_response("The answer is 42."),
            ]
        )

        engine = AgentEngine(
            provider=provider,
            cost_tracker=cost_tracker,
        )
        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=5,
        )

        # Successful single-turn completion
        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.total_turns == 1

        # Completion summary matches the response
        assert result.completion_summary == "The answer is 42."

        # No tool messages in conversation
        conversation = result.execution_result.context.conversation
        assert len(conversation) >= 3  # system + user + assistant minimum
        tool_msgs = [m for m in conversation if m.role == MessageRole.TOOL]
        assert len(tool_msgs) == 0

        # Task lifecycle: ASSIGNED -> IN_PROGRESS -> IN_REVIEW (review gate)
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.IN_REVIEW
        assert len(task_execution.transition_log) == 2

        # Cost tracking
        total_cost = await cost_tracker.get_total_cost()
        assert total_cost == pytest.approx(result.total_cost)
        assert await cost_tracker.get_record_count() == 1

        # IDs and duration
        assert result.agent_id == str(identity.id)
        assert result.task_id == str(task.id)
        assert result.duration_seconds > 0


class TestPermissionDeniedRecovery:
    """Agent recovers gracefully after a tool permission denial."""

    async def test_custom_access_denies_tool_and_agent_recovers(
        self, e2e_workspace: Path
    ) -> None:
        """CUSTOM access with empty allowed list denies all tools.

        The agent receives a permission denied error for the tool call,
        then the LLM responds with a text explanation (recovery).
        """
        write_tool = WriteFileTool(workspace_root=e2e_workspace)
        registry = ToolRegistry([write_tool])
        cost_tracker = CostTracker()

        identity = make_e2e_identity(
            tools=ToolPermissions(
                access_level=ToolAccessLevel.CUSTOM,
                allowed=(),
            ),
        )
        task = make_e2e_task(
            identity=identity,
            title="Try writing a file",
            description="Attempt to write output.txt.",
        )

        provider = ScriptedProvider(
            [
                # Turn 1: LLM tries to call write_file (will be denied)
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id="call-denied",
                            name="write_file",
                            arguments={
                                "path": "output.txt",
                                "content": "Should not be written",
                            },
                        ),
                    ),
                ),
                # Turn 2: LLM recovers with a text explanation
                make_text_response("I don't have permission to write files."),
            ]
        )

        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
            cost_tracker=cost_tracker,
        )
        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=5,
        )

        # Agent recovered successfully
        assert result.is_success is True
        assert result.total_turns == 2

        # Tool message has permission denied error
        conversation = result.execution_result.context.conversation
        tool_msgs = [m for m in conversation if m.role == MessageRole.TOOL]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].tool_result is not None
        assert tool_msgs[0].tool_result.is_error is True
        assert "Permission denied" in tool_msgs[0].tool_result.content

        # File was NOT created on disk
        assert not (e2e_workspace / "output.txt").exists()

        # Task at IN_REVIEW (agent completed, awaiting review)
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.IN_REVIEW

        # Cost tracking records both turns
        assert await cost_tracker.get_record_count() == 2
        total_cost = await cost_tracker.get_total_cost()
        assert total_cost == pytest.approx(result.total_cost)

        # IDs and duration
        assert result.agent_id == str(identity.id)
        assert result.task_id == str(task.id)
        assert result.duration_seconds > 0


class TestMaxTurnsExhausted:
    """Agent exhausts max_turns without completing."""

    async def test_max_turns_terminates_cleanly(self, e2e_workspace: Path) -> None:
        """Agent makes tool calls on both turns, never finishing.

        With max_turns=2, after turn 2 the loop exits with MAX_TURNS.
        Task stays IN_PROGRESS (not FAILED, not COMPLETED).
        """
        write_tool = WriteFileTool(workspace_root=e2e_workspace)
        registry = ToolRegistry([write_tool])
        cost_tracker = CostTracker()

        identity = make_e2e_identity()
        task = make_e2e_task(
            identity=identity,
            title="Infinite tool calls",
            description="Keep calling tools forever.",
        )

        provider = ScriptedProvider(
            [
                # Turn 1: tool call
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id="call-loop-1",
                            name="write_file",
                            arguments={
                                "path": "file1.txt",
                                "content": "turn 1",
                            },
                        ),
                    ),
                ),
                # Turn 2: another tool call
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id="call-loop-2",
                            name="write_file",
                            arguments={
                                "path": "file2.txt",
                                "content": "turn 2",
                            },
                        ),
                    ),
                ),
                # Turn 3: would not be consumed
                make_text_response("Should never reach this."),
            ]
        )

        engine = AgentEngine(
            provider=provider,
            tool_registry=registry,
            cost_tracker=cost_tracker,
        )
        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=2,
        )

        # MAX_TURNS termination -- not a success
        assert result.is_success is False
        assert result.termination_reason == TerminationReason.MAX_TURNS
        assert result.total_turns == 2

        # Task stays IN_PROGRESS (only COMPLETED/SHUTDOWN/ERROR trigger transitions)
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.IN_PROGRESS

        # No error message for MAX_TURNS
        assert result.execution_result.error_message is None

        # Provider was called exactly twice
        assert provider.call_count == 2

        # Files were actually written to disk
        assert (e2e_workspace / "file1.txt").exists()
        assert (e2e_workspace / "file2.txt").exists()

        # Tool results are present in conversation (tools were executed)
        conversation = result.execution_result.context.conversation
        tool_msgs = [m for m in conversation if m.role == MessageRole.TOOL]
        assert len(tool_msgs) == 2

        # Cost tracking records both turns
        assert await cost_tracker.get_record_count() == 2
        total_cost = await cost_tracker.get_total_cost()
        assert total_cost == pytest.approx(result.total_cost)

        # IDs and duration
        assert result.agent_id == str(identity.id)
        assert result.task_id == str(task.id)
        assert result.duration_seconds > 0


@pytest.mark.slow
@pytest.mark.timeout(60)
@pytest.mark.skipif(
    os.environ.get("REAL_LLM_TEST") != "1",
    reason="Set REAL_LLM_TEST=1 to run real LLM integration test",
)
class TestRealLLMIntegration:
    """Optional smoke test with a real LLM provider.

    Skipped unless REAL_LLM_TEST=1 is set; not expected to run in CI.
    Each method also requires REAL_LLM_MODEL and REAL_LLM_PROVIDER so
    the test can construct a configured provider driver without
    leaning on app-startup config wiring. Authentication is configured
    via at least one of REAL_LLM_API_KEY (hosted providers) or
    REAL_LLM_BASE_URL (local / self-hosted providers); when both are
    set, both are forwarded to the provider driver (api_key for auth,
    base_url as the request endpoint).
    """

    async def test_real_provider_text_completion(self) -> None:
        """Minimal text-only task end-to-end through the configured provider driver."""
        provider_model = os.environ.get("REAL_LLM_MODEL")
        if not provider_model:
            pytest.skip(
                "Set REAL_LLM_MODEL to a valid model ID "
                "(e.g. 'example-large-001') to run this test"
            )
        provider_name = os.environ.get("REAL_LLM_PROVIDER")
        if not provider_name:
            pytest.skip(
                "Set REAL_LLM_PROVIDER to a provider routing key "
                "(e.g. 'example-provider') to run this test"
            )
        # Normalise empty-string env vars to None so ProviderConfig's
        # NotBlankStr fields accept the value; an exported-but-empty
        # var is treated as "unset" rather than rejected at construct.
        api_key = os.environ.get("REAL_LLM_API_KEY") or None
        base_url = os.environ.get("REAL_LLM_BASE_URL") or None
        if api_key is None and base_url is None:
            pytest.skip(
                "Set REAL_LLM_API_KEY (hosted) or REAL_LLM_BASE_URL "
                "(local provider) to run this test"
            )

        provider_config = ProviderConfig(
            litellm_provider=provider_name,
            api_key=api_key,
            base_url=base_url,
            models=(ProviderModelConfig(id=provider_model),),
        )
        provider = LiteLLMDriver(provider_name, provider_config)

        cost_tracker = CostTracker()
        identity = make_e2e_identity().model_copy(
            update={
                "model": ModelConfig(
                    provider=provider_name,
                    model_id=provider_model,
                ),
            },
        )
        task = make_e2e_task(
            identity=identity,
            title="Real LLM smoke test",
            description="Reply with the single word 'ack'.",
        )

        engine = AgentEngine(
            provider=provider,
            cost_tracker=cost_tracker,
        )
        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=2,
        )

        # Real provider produced a successful single-turn completion.
        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.completion_summary
        # ``>= 0`` (not ``> 0``) so a local zero-cost preset still passes.
        assert result.total_cost >= 0
        assert await cost_tracker.get_record_count() == result.total_turns
        assert result.task_id == str(task.id)
        assert result.duration_seconds > 0
