"""Tests for RequestClarificationTool."""

from typing import cast

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource
from synthorg.tools.clarification_tool import RequestClarificationTool
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


@pytest.fixture
def approval_store() -> ApprovalStore:
    return ApprovalStore()


@pytest.fixture
def tool(approval_store: ApprovalStore) -> RequestClarificationTool:
    return RequestClarificationTool(
        approval_store=approval_store,
        agent_id="agent-1",
        task_id="task-1",
    )


class TestToolCreation:
    """Tool creation and schema."""

    def test_name(self, tool: RequestClarificationTool) -> None:
        assert tool.name == "request_clarification"

    def test_has_question_param(self, tool: RequestClarificationTool) -> None:
        schema = tool.parameters_schema
        assert schema is not None
        props = cast("JsonDict", schema)["properties"]
        assert "question" in props
        assert schema["required"] == ["question"]


class TestExecute:
    """Execution parks with the clarification marker set."""

    async def test_creates_clarification_item(
        self,
        tool: RequestClarificationTool,
        approval_store: ApprovalStore,
    ) -> None:
        result = await tool.execute(
            arguments={"question": "Which database backend should I target?"},
        )
        assert not result.is_error
        assert result.metadata["requires_parking"] is True
        assert result.metadata["clarification"] is True

        item = await approval_store.get(
            cast("JsonDict", result.metadata)["approval_id"]
        )
        assert item is not None
        # Reuses PARKED_CONTEXT so the mid-execution resume path restores
        # the run and injects the human's answer.
        assert item.source is ApprovalSource.PARKED_CONTEXT
        assert item.risk_level is ApprovalRiskLevel.LOW
        assert item.description == "Which database backend should I target?"
        assert item.requested_by == "agent-1"
        assert item.task_id == "task-1"
        assert item.metadata["clarification"] == "true"

    async def test_question_in_content(
        self,
        tool: RequestClarificationTool,
    ) -> None:
        result = await tool.execute(
            arguments={"question": "Use metric or imperial units?"},
        )
        assert "Use metric or imperial units?" in result.content

    async def test_no_task_id(
        self,
        approval_store: ApprovalStore,
    ) -> None:
        tool = RequestClarificationTool(
            approval_store=approval_store,
            agent_id="agent-1",
            task_id=None,
        )
        result = await tool.execute(arguments={"question": "Anything unclear?"})
        assert not result.is_error
        item = await approval_store.get(
            cast("JsonDict", result.metadata)["approval_id"]
        )
        assert item is not None
        assert item.task_id is None

    async def test_blank_question_rejected(
        self,
        tool: RequestClarificationTool,
    ) -> None:
        result = await tool.execute(arguments={"question": "   "})
        assert result.is_error
        assert "non-empty" in result.content


class TestErrorHandling:
    """Graceful error handling on store failure."""

    async def test_store_error_returns_error_result(
        self,
        approval_store: ApprovalStore,
        tool: RequestClarificationTool,
    ) -> None:
        async def _failing_add(item: object) -> None:
            msg = "Store unavailable"
            raise RuntimeError(msg)

        approval_store.add = _failing_add  # type: ignore[method-assign]

        result = await tool.execute(arguments={"question": "Proceed?"})
        assert result.is_error
        assert "Failed to create clarification request" in result.content
