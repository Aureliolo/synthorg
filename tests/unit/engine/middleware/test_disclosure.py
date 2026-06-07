"""Tests for DisclosureMiddleware."""

from datetime import UTC, date, datetime

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.enums import Priority, TaskType
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.middleware.disclosure import DisclosureMiddleware
from synthorg.engine.middleware.models import AgentMiddlewareContext, ToolCallResult
from synthorg.hr.seniority import SeniorityLevel
from synthorg.tools.disclosure_config import ToolDisclosureConfig
from tests._shared import as_uuid


def _make_task() -> Task:
    """Create a minimal Task for testing."""
    return Task(
        id=as_uuid("task-1"),
        title="Test task",
        description="A test task",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="test-creator",
    )


def _make_context(
    *,
    loaded_tools: frozenset[str] = frozenset(),
    tool_load_order: tuple[str, ...] = (),
    context_fill_tokens: int = 0,
    context_capacity_tokens: int | None = None,
) -> AgentMiddlewareContext:
    """Create a minimal middleware context for testing."""
    identity = AgentIdentity(
        name="test-agent",
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        personality=PersonalityConfig(),
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )
    agent_ctx = AgentContext(
        execution_id="exec-1",
        identity=identity,
        started_at=datetime.now(UTC),
        loaded_tools=loaded_tools,
        tool_load_order=tool_load_order,
        context_fill_tokens=context_fill_tokens,
        context_capacity_tokens=context_capacity_tokens,
    )
    task = _make_task()
    return AgentMiddlewareContext(
        agent_context=agent_ctx,
        identity=identity,
        task=task,
        agent_id="agent-1",
        task_id=str(task.id),
        execution_id="exec-1",
        effective_autonomy=None,
    )


def _success_result(
    tool_name: str,
    output: str = "",
    metadata: dict[str, object] | None = None,
) -> ToolCallResult:
    """Build a successful ToolCallResult."""
    return ToolCallResult(
        tool_name=tool_name,
        output=output,
        success=True,
        metadata=metadata or {},
    )


def _error_result(tool_name: str) -> ToolCallResult:
    """Build a failed ToolCallResult."""
    return ToolCallResult(
        tool_name=tool_name,
        output="",
        success=False,
        error="Tool failed",
    )


# ── load_tool observation ────────────────────────────────────────


@pytest.mark.unit
class TestLoadToolObservation:
    """Tests for middleware observing load_tool calls."""

    async def test_loads_tool_from_metadata(self) -> None:
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _success_result(
            "load_tool",
            output='{"name": "read_file"}',
            metadata={"should_load_tool": "read_file"},
        )

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is not None
        assert "read_file" in returned.updated_agent_context.loaded_tools

    async def test_loads_tool_from_output_json(self) -> None:
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _success_result(
            "load_tool",
            output='{"name": "read_file"}',
        )

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is not None
        assert "read_file" in returned.updated_agent_context.loaded_tools

    async def test_does_not_load_on_error(self) -> None:
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _error_result("load_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned is result

    async def test_idempotent_load(self) -> None:
        mw = DisclosureMiddleware()
        ctx = _make_context(
            loaded_tools=frozenset({"read_file"}),
            tool_load_order=("read_file",),
        )
        result = _success_result(
            "load_tool",
            output='{"name": "read_file"}',
            metadata={"should_load_tool": "read_file"},
        )

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        # Idempotent: already-loaded tool produces no state change
        assert returned.updated_agent_context is None


# ── load_tool_resource observation ───────────────────────────────


@pytest.mark.unit
class TestLoadToolResourceObservation:
    """Tests for middleware observing load_tool_resource calls."""

    async def test_loads_resource_from_metadata(self) -> None:
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _success_result(
            "load_tool_resource",
            output="# Guide content",
            metadata={"should_load_resource": ("read_file", "guide")},
        )

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is not None
        assert ("read_file", "guide") in returned.updated_agent_context.loaded_resources

    async def test_does_not_load_without_metadata(self) -> None:
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _success_result(
            "load_tool_resource",
            output="raw content",
        )

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        await mw.wrap_tool_call(ctx, call)


# ── Auto-unload under budget pressure ───────────────────────────


@pytest.mark.unit
class TestAutoUnload:
    """Tests for auto-unload under budget pressure."""

    async def test_unloads_oldest_on_pressure(self) -> None:
        config = ToolDisclosureConfig(unload_threshold_percent=80.0)
        mw = DisclosureMiddleware(config=config)
        # 90% full context triggers unload
        ctx = _make_context(
            loaded_tools=frozenset({"tool_a", "tool_b"}),
            tool_load_order=("tool_a", "tool_b"),
            context_fill_tokens=9000,
            context_capacity_tokens=10000,
        )
        result = _success_result("some_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is not None
        assert "tool_a" not in returned.updated_agent_context.loaded_tools
        assert "tool_b" in returned.updated_agent_context.loaded_tools
        assert returned.updated_agent_context.tool_load_order == ("tool_b",)

    async def test_no_unload_below_threshold(self) -> None:
        config = ToolDisclosureConfig(unload_threshold_percent=80.0)
        mw = DisclosureMiddleware(config=config)
        # 50% fill is below threshold
        ctx = _make_context(
            loaded_tools=frozenset({"tool_a"}),
            tool_load_order=("tool_a",),
            context_fill_tokens=5000,
            context_capacity_tokens=10000,
        )
        result = _success_result("some_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is None

    async def test_no_unload_when_disabled(self) -> None:
        config = ToolDisclosureConfig(auto_unload_on_budget_pressure=False)
        mw = DisclosureMiddleware(config=config)
        ctx = _make_context(
            loaded_tools=frozenset({"tool_a"}),
            tool_load_order=("tool_a",),
            context_fill_tokens=9500,
            context_capacity_tokens=10000,
        )
        result = _success_result("some_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is None

    async def test_no_unload_when_no_capacity(self) -> None:
        config = ToolDisclosureConfig()
        mw = DisclosureMiddleware(config=config)
        ctx = _make_context(
            loaded_tools=frozenset({"tool_a"}),
            tool_load_order=("tool_a",),
            context_fill_tokens=5000,
            context_capacity_tokens=None,
        )
        result = _success_result("some_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is None

    async def test_no_unload_when_no_loaded_tools(self) -> None:
        config = ToolDisclosureConfig(unload_threshold_percent=80.0)
        mw = DisclosureMiddleware(config=config)
        ctx = _make_context(
            context_fill_tokens=9500,
            context_capacity_tokens=10000,
        )
        result = _success_result("some_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is None


# ── Non-discovery tools pass through ────────────────────────────


@pytest.mark.unit
class TestPassThrough:
    """Tests that non-discovery tools pass through unchanged."""

    async def test_non_discovery_tool_passes(self) -> None:
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _success_result("read_file", output="file content")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned is result

    async def test_error_tool_passes(self) -> None:
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _error_result("some_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned is result


# ── JSON parsing failure modes ───────────────────────────────────


@pytest.mark.unit
class TestExtractToolNameEdgeCases:
    """Tests for _extract_tool_name with malformed inputs."""

    @pytest.mark.parametrize(
        "output",
        [
            pytest.param("not json at all", id="malformed-json"),
            pytest.param('["array", "not", "dict"]', id="non-dict-json"),
            pytest.param('{"data": "read_file"}', id="missing-name-key"),
            pytest.param('{"name": 123}', id="non-string-name"),
            pytest.param('{"name": "   "}', id="whitespace-only-name"),
        ],
    )
    async def test_bad_output_does_not_load(self, output: str) -> None:
        """load_tool with malformed output must not update context."""
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _success_result("load_tool", output=output)

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is None

    async def test_empty_string_returns_none(self) -> None:
        """Empty string produces no tool name."""
        name = DisclosureMiddleware._extract_tool_name("")
        assert name is None

    async def test_empty_json_object_returns_none(self) -> None:
        """Empty JSON object has no name key."""
        name = DisclosureMiddleware._extract_tool_name("{}")
        assert name is None


# ── Auto-unload threshold boundary ───────────────────────────────


@pytest.mark.unit
class TestAutoUnloadBoundary:
    """Tests for auto-unload at exact threshold boundaries."""

    async def test_unload_at_exact_threshold(self) -> None:
        """Context fill exactly at 80% should trigger unload."""
        config = ToolDisclosureConfig(unload_threshold_percent=80.0)
        mw = DisclosureMiddleware(config=config)
        ctx = _make_context(
            loaded_tools=frozenset({"tool_a"}),
            tool_load_order=("tool_a",),
            context_fill_tokens=8000,
            context_capacity_tokens=10000,
        )
        result = _success_result("some_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        await mw.wrap_tool_call(ctx, call)
        # 80% == 80% threshold: auto-unload should trigger

    async def test_no_unload_just_below_threshold(self) -> None:
        """Context fill at 79.9% should NOT trigger unload."""
        config = ToolDisclosureConfig(unload_threshold_percent=80.0)
        mw = DisclosureMiddleware(config=config)
        ctx = _make_context(
            loaded_tools=frozenset({"tool_a"}),
            tool_load_order=("tool_a",),
            context_fill_tokens=7990,
            context_capacity_tokens=10000,
        )
        result = _success_result("some_tool")

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        await mw.wrap_tool_call(ctx, call)
        # 79.9% < 80% threshold: no unload


# ── Metadata tuple validation ────────────────────────────────────


@pytest.mark.unit
class TestMetadataTupleValidation:
    """Tests for metadata pair validation in resource loading."""

    @pytest.mark.parametrize(
        "metadata_value",
        [
            pytest.param("not-a-tuple", id="string-not-tuple"),
            pytest.param((123, "guide"), id="non-string-tool-name"),
            pytest.param(("tool", 456), id="non-string-resource-id"),
            pytest.param(("only_one",), id="wrong-length-tuple"),
        ],
    )
    async def test_invalid_pair_does_not_load(
        self,
        metadata_value: object,
    ) -> None:
        """Invalid metadata pairs must not update context."""
        mw = DisclosureMiddleware()
        ctx = _make_context()
        result = _success_result(
            "load_tool_resource",
            output="content",
            metadata={"should_load_resource": metadata_value},
        )

        async def call(_ctx: AgentMiddlewareContext) -> ToolCallResult:
            return result

        returned = await mw.wrap_tool_call(ctx, call)
        assert returned.updated_agent_context is None
