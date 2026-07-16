"""Tests for RequestProjectDecisionTool."""

import json
from typing import cast

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource
from synthorg.tools.decision_tool import RequestProjectDecisionTool
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


@pytest.fixture
def approval_store() -> ApprovalStore:
    return ApprovalStore()


@pytest.fixture
def tool(approval_store: ApprovalStore) -> RequestProjectDecisionTool:
    return RequestProjectDecisionTool(
        approval_store=approval_store,
        agent_id="agent-1",
        task_id="task-1",
    )


class TestToolCreation:
    """Tool creation and schema."""

    def test_name(self, tool: RequestProjectDecisionTool) -> None:
        assert tool.name == "request_project_decision"

    def test_has_question_and_options(self, tool: RequestProjectDecisionTool) -> None:
        schema = tool.parameters_schema
        assert schema is not None
        props = cast("JsonDict", schema)["properties"]
        assert "question" in props
        assert "options" in props
        assert schema["required"] == ["question"]


def _rich_options() -> list[dict[str, object]]:
    """Two rich options (one recommended) for a decision fork."""
    return [
        {
            "id": "react",
            "title": "React",
            "summary": "Mature ecosystem, larger bundle, familiar to the team.",
            "recommended": True,
        },
        {
            "id": "svelte",
            "title": "Svelte",
            "summary": "Small bundle, compiler-based, smaller ecosystem.",
        },
    ]


class TestExecute:
    """Execution parks with the clarification + decision markers set."""

    async def test_creates_decision_item_with_options(
        self,
        tool: RequestProjectDecisionTool,
        approval_store: ApprovalStore,
    ) -> None:
        result = await tool.execute(
            arguments={
                "question": "Which web framework should we target?",
                "options": _rich_options(),
            },
        )
        assert not result.is_error
        assert result.metadata["requires_parking"] is True
        # Parks like a clarification (task -> AWAITING_INPUT) AND is a decision
        # (answer -> project-brain DECISION entry on resume).
        assert result.metadata["clarification"] is True
        assert result.metadata["decision"] is True
        assert result.metadata["action_type"] == "decision:project"

        item = await approval_store.get(
            cast("JsonDict", result.metadata)["approval_id"]
        )
        assert item is not None
        assert item.source is ApprovalSource.PARKED_CONTEXT
        assert item.risk_level is ApprovalRiskLevel.LOW
        assert item.action_type == "decision:project"
        assert item.description == "Which web framework should we target?"
        assert item.metadata["decision"] == "true"
        # The brain-record alternatives are the option titles.
        assert json.loads(item.metadata["options"]) == ["React", "Svelte"]
        # The rich per-option writeups ride on the evidence package the
        # operator picks from.
        assert item.evidence_package is not None
        opt_ids = [o.id for o in item.evidence_package.options]
        assert opt_ids == ["react", "svelte"]
        assert sum(o.recommended for o in item.evidence_package.options) == 1

    async def test_open_ended_has_no_evidence(
        self,
        tool: RequestProjectDecisionTool,
        approval_store: ApprovalStore,
    ) -> None:
        result = await tool.execute(
            arguments={"question": "What should the release cadence be?"},
        )
        assert not result.is_error
        item = await approval_store.get(
            cast("JsonDict", result.metadata)["approval_id"]
        )
        assert item is not None
        assert item.evidence_package is None
        assert json.loads(item.metadata["options"]) == []

    async def test_option_titles_in_content(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        result = await tool.execute(
            arguments={"question": "Framework?", "options": _rich_options()},
        )
        assert "React, Svelte" in result.content

    async def test_long_question_yields_short_title(
        self,
        tool: RequestProjectDecisionTool,
        approval_store: ApprovalStore,
    ) -> None:
        long_question = "Which framework? " + "x" * 400
        result = await tool.execute(
            arguments={"question": long_question, "options": _rich_options()},
        )
        assert not result.is_error
        item = await approval_store.get(
            cast("JsonDict", result.metadata)["approval_id"]
        )
        assert item is not None
        assert item.evidence_package is not None
        # The title is a compact label; the full question rides on narrative.
        assert len(item.evidence_package.title) <= 120
        assert item.evidence_package.narrative == long_question
        assert item.description == long_question

    async def test_invalid_options_no_recommended_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "summary": "first"},
            {"id": "b", "title": "B", "summary": "second"},
        ]
        result = await tool.execute(
            arguments={"question": "Framework?", "options": options},
        )
        # The args model validator rejects at parse time.
        assert result.is_error
        assert "Invalid decision arguments" in result.content
        assert "recommended" in result.content

    async def test_two_recommended_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "summary": "first", "recommended": True},
            {"id": "b", "title": "B", "summary": "second", "recommended": True},
        ]
        result = await tool.execute(
            arguments={"question": "Framework?", "options": options},
        )
        assert result.is_error
        assert "recommended" in result.content

    async def test_duplicate_option_ids_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "summary": "first", "recommended": True},
            {"id": "a", "title": "B", "summary": "second"},
        ]
        result = await tool.execute(
            arguments={"question": "Framework?", "options": options},
        )
        assert result.is_error
        assert "duplicate option ids" in result.content

    async def test_too_many_options_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": f"o{i}", "title": f"O{i}", "summary": "x", "recommended": i == 0}
            for i in range(13)
        ]
        result = await tool.execute(
            arguments={"question": "Framework?", "options": options},
        )
        assert result.is_error
        assert "Invalid decision arguments" in result.content

    async def test_malformed_option_schema_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "recommended": True},  # missing summary
            {"id": "b", "title": "B", "summary": "second"},
        ]
        result = await tool.execute(
            arguments={"question": "Framework?", "options": options},
        )
        assert result.is_error
        assert "Invalid decision arguments" in result.content
        assert "summary" in result.content

    async def test_single_option_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        options = [
            {"id": "a", "title": "A", "summary": "only one", "recommended": True}
        ]
        result = await tool.execute(
            arguments={"question": "Framework?", "options": options},
        )
        assert result.is_error
        assert "at least two options" in result.content

    async def test_blank_question_rejected(
        self,
        tool: RequestProjectDecisionTool,
    ) -> None:
        result = await tool.execute(arguments={"question": "   "})
        assert result.is_error

    async def test_store_error_returns_error_result(
        self,
        approval_store: ApprovalStore,
        tool: RequestProjectDecisionTool,
    ) -> None:
        async def _failing_add(item: object) -> None:
            msg = "Store unavailable"
            raise RuntimeError(msg)

        approval_store.add = _failing_add  # type: ignore[method-assign]

        result = await tool.execute(arguments={"question": "Framework?"})
        assert result.is_error
        assert "Failed to create decision request" in result.content
