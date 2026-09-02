"""E2E tests: single agent completes real tasks end-to-end.

Validates the core MVP hypothesis -- a single agent can complete a real
task through the full execution pipeline (engine, execution loop, real tools,
cost tracking, task lifecycle).
"""

import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from dataclasses import replace

from synthorg.budget.tracker import CostTracker
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.agent import ModelConfig, ToolPermissions
from synthorg.core.task_enums import TaskStatus
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.engine.loop_budget_defaults import DEFAULT_BUDGET_SIGNAL_TERMINAL_PERCENT
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.providers.drivers.litellm_driver import LiteLLMDriver
from synthorg.providers.enums import AuthType, MessageRole
from synthorg.providers.models import ToolCall
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry
from tests._shared import (
    UNWIRED_BUDGET,
    engine_with,
    make_in_memory_catalog,
    mock_of,
    unwired_core,
)

from .conftest import (
    ScriptedProvider,
    e2e_tool_workspace,
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

        engine = engine_with(
            provider,
            core=replace(unwired_core(provider), tool_registry=registry),
            budget=replace(UNWIRED_BUDGET, cost_tracker=cost_tracker),
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
        output_file = e2e_tool_workspace(e2e_workspace) / "output.txt"
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

        engine = engine_with(
            provider, budget=replace(UNWIRED_BUDGET, cost_tracker=cost_tracker)
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

        engine = engine_with(
            provider,
            core=replace(unwired_core(provider), tool_registry=registry),
            budget=replace(UNWIRED_BUDGET, cost_tracker=cost_tracker),
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
        assert not (e2e_tool_workspace(e2e_workspace) / "output.txt").exists()

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


def _writing_forever(count: int) -> ScriptedProvider:
    """Script *count* tool-call turns, then one that would finish.

    Args:
        count: How many turns call a tool without finishing.

    Returns:
        A provider whose final response is only reached if the loop ran
        longer than the ceiling under test.
    """
    return ScriptedProvider(
        [
            *(
                make_tool_call_response(
                    tool_calls=(
                        ToolCall(
                            id=f"call-loop-{turn}",
                            name="write_file",
                            arguments={
                                "path": f"file{turn}.txt",
                                "content": f"turn {turn}",
                            },
                        ),
                    ),
                )
                for turn in range(1, count + 1)
            ),
            make_text_response("Should never reach this."),
        ]
    )


def _extensions(count: int) -> ConfigResolver:
    """Resolve ``engine.max_turn_extensions`` to *count*, everything else to 0.

    Args:
        count: Extensions the run may grant itself.

    Returns:
        A resolver double. Zero is a meaningful answer for every other
        integer setting the engine reads here (each treats it as unset and
        falls back to its own default), except
        ``engine.budget_signal_terminal_percent``: unlike its siblings, that
        one setting has no zero-disables reading (``BudgetSignalConfig``
        requires it strictly positive), so an unset resolution must mirror
        what a real resolver returns for an unset key -- its own declared
        default -- rather than the blanket 0 every other key here treats as
        unset.
    """

    async def _get_int(namespace: str, key: str) -> int:
        if (namespace, key) == ("engine", "max_turn_extensions"):
            return count
        if (namespace, key) == ("engine", "budget_signal_terminal_percent"):
            return DEFAULT_BUDGET_SIGNAL_TERMINAL_PERCENT
        return 0

    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_int=AsyncMock(side_effect=_get_int),
        get_bool=AsyncMock(return_value=True),
    )
    return resolver


class TestMaxTurnsExhausted:
    """Agent exhausts max_turns without completing."""

    async def test_max_turns_terminates_cleanly(self, e2e_workspace: Path) -> None:
        """Agent makes tool calls on both turns, never finishing.

        With max_turns=2 and the extension budget at zero, the first ceiling
        ends the run with MAX_TURNS. The task terminalises to FAILED (not
        COMPLETED, and never left at IN_PROGRESS): a run that spent its
        turns without finishing has stopped, and only a terminal status
        makes it a stall the replan trigger can see.
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

        provider = _writing_forever(2)

        engine = engine_with(
            provider,
            core=replace(
                unwired_core(provider),
                tool_registry=registry,
                config_resolver=_extensions(0),
            ),
            budget=replace(UNWIRED_BUDGET, cost_tracker=cost_tracker),
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

        # A run that stopped without finishing is FAILED, not still moving:
        # left at IN_PROGRESS the stall derivation reads it as in flight, so
        # its initiative could never be replanned or completed.
        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.FAILED

        # No error message for MAX_TURNS
        assert result.execution_result.error_message is None

        # Provider was called exactly twice
        assert provider.call_count == 2

        # Files were actually written to disk
        written = e2e_tool_workspace(e2e_workspace)
        assert (written / "file1.txt").exists()
        assert (written / "file2.txt").exists()

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

    async def test_extensions_run_out_and_the_park_cannot_be_armed(
        self, e2e_workspace: Path
    ) -> None:
        """Each ceiling buys the original budget again, four times over.

        From two turns the run reaches 4, then 6, then 8, because every
        extension is worth the budget the operator configured rather than a
        second number nobody tuned. Then it has to park, and this engine has
        no approval store to park into: rather than leave the task in
        AWAITING_INPUT with nothing able to answer it, the run ends MAX_TURNS
        and terminalises FAILED, which the stall derivation can still see.
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

        provider = _writing_forever(8)

        engine = engine_with(
            provider,
            core=replace(
                unwired_core(provider),
                tool_registry=registry,
                config_resolver=_extensions(3),
            ),
            budget=replace(UNWIRED_BUDGET, cost_tracker=cost_tracker),
        )
        result = await engine.run(
            identity=identity,
            task=task,
            max_turns=2,
        )

        assert result.total_turns == 8
        assert result.termination_reason == TerminationReason.MAX_TURNS
        assert result.is_success is False
        assert result.is_awaiting_human is False

        task_execution = result.execution_result.context.task_execution
        assert task_execution is not None
        assert task_execution.status == TaskStatus.FAILED

        # Everything the run wrote survives the ceiling, which is the whole
        # reason extensions exist.
        written = e2e_tool_workspace(e2e_workspace)
        assert all((written / f"file{turn}.txt").exists() for turn in range(1, 9))


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
                "(e.g. 'example-expert-001') to run this test"
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

        # Catalog-only credentials: when an API key is supplied, mint it
        # into an in-memory connection catalog and reference it via
        # connection_name; the driver resolves it at call time. A
        # base-URL-only local provider needs no credential (NONE auth).
        catalog: ConnectionCatalog | None = None
        connection_name: str | None = None
        if api_key is not None:
            catalog = make_in_memory_catalog()
            connection_name = "e2e-provider-credential"
            await catalog.create(
                name=connection_name,
                connection_type=ConnectionType.LLM_PROVIDER,
                auth_method=AuthMethod.API_KEY.value,
                credentials={"api_key": api_key},
            )

        provider_config = ProviderConfig(
            litellm_provider=provider_name,
            auth_type=AuthType.API_KEY if api_key is not None else AuthType.NONE,
            connection_name=connection_name,
            base_url=base_url,
            models=(ProviderModelConfig(id=provider_model),),
        )
        provider = LiteLLMDriver(
            provider_name,
            provider_config,
            connection_catalog=catalog,
        )

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

        engine = engine_with(
            provider, budget=replace(UNWIRED_BUDGET, cost_tracker=cost_tracker)
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
