"""Tests for RequestHumanApprovalTool."""

from unittest.mock import MagicMock

import pytest

from ai_company.api.approval_store import ApprovalStore
from ai_company.core.enums import ApprovalRiskLevel
from ai_company.security.timeout.risk_tier_classifier import DefaultRiskTierClassifier
from ai_company.tools.approval_tool import RequestHumanApprovalTool

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.fixture
def approval_store() -> ApprovalStore:
    return ApprovalStore()


@pytest.fixture
def tool(approval_store: ApprovalStore) -> RequestHumanApprovalTool:
    return RequestHumanApprovalTool(
        approval_store=approval_store,
        agent_id="agent-1",
        task_id="task-1",
    )


@pytest.fixture
def tool_with_classifier(
    approval_store: ApprovalStore,
) -> RequestHumanApprovalTool:
    return RequestHumanApprovalTool(
        approval_store=approval_store,
        risk_classifier=DefaultRiskTierClassifier(),
        agent_id="agent-1",
        task_id="task-1",
    )


class TestToolCreation:
    """Tool creation with valid parameters."""

    def test_name(self, tool: RequestHumanApprovalTool) -> None:
        assert tool.name == "request_human_approval"

    def test_action_type(self, tool: RequestHumanApprovalTool) -> None:
        assert tool.action_type == "comms:internal"

    def test_has_parameters_schema(self, tool: RequestHumanApprovalTool) -> None:
        schema = tool.parameters_schema
        assert schema is not None
        assert "action_type" in schema["properties"]
        assert "title" in schema["properties"]
        assert "description" in schema["properties"]
        assert schema["required"] == ["action_type", "title", "description"]


class TestExecute:
    """Tool execution creates ApprovalItem and returns parking metadata."""

    async def test_creates_approval_item(
        self,
        tool: RequestHumanApprovalTool,
        approval_store: ApprovalStore,
    ) -> None:
        result = await tool.execute(
            arguments={
                "action_type": "deploy:production",
                "title": "Deploy v2.0",
                "description": "Deploy version 2.0 to production",
            },
        )
        assert not result.is_error
        assert result.metadata["requires_parking"] is True
        assert "approval_id" in result.metadata

        # Verify item was created in store
        item = await approval_store.get(result.metadata["approval_id"])
        assert item is not None
        assert item.action_type == "deploy:production"
        assert item.title == "Deploy v2.0"
        assert item.requested_by == "agent-1"
        assert item.task_id == "task-1"

    async def test_returns_requires_parking_metadata(
        self,
        tool: RequestHumanApprovalTool,
    ) -> None:
        result = await tool.execute(
            arguments={
                "action_type": "deploy:production",
                "title": "Deploy v2.0",
                "description": "Full deployment",
            },
        )
        assert result.metadata["requires_parking"] is True
        assert isinstance(result.metadata["approval_id"], str)
        assert result.metadata["action_type"] == "deploy:production"
        assert result.metadata["risk_level"] == "high"

    async def test_default_risk_level_is_high(
        self,
        tool: RequestHumanApprovalTool,
    ) -> None:
        result = await tool.execute(
            arguments={
                "action_type": "deploy:production",
                "title": "Deploy v2.0",
                "description": "Full deployment",
            },
        )
        assert result.metadata["risk_level"] == "high"

    async def test_content_includes_approval_id(
        self,
        tool: RequestHumanApprovalTool,
    ) -> None:
        result = await tool.execute(
            arguments={
                "action_type": "deploy:production",
                "title": "Deploy v2.0",
                "description": "Full deployment",
            },
        )
        assert result.metadata["approval_id"] in result.content

    async def test_no_task_id(
        self,
        approval_store: ApprovalStore,
    ) -> None:
        tool = RequestHumanApprovalTool(
            approval_store=approval_store,
            agent_id="agent-1",
            task_id=None,
        )
        result = await tool.execute(
            arguments={
                "action_type": "deploy:staging",
                "title": "Deploy staging",
                "description": "Deploy to staging env",
            },
        )
        assert not result.is_error
        item = await approval_store.get(result.metadata["approval_id"])
        assert item is not None
        assert item.task_id is None


class TestRiskClassification:
    """Risk classification with and without classifier."""

    async def test_with_classifier_uses_known_action(
        self,
        tool_with_classifier: RequestHumanApprovalTool,
    ) -> None:
        result = await tool_with_classifier.execute(
            arguments={
                "action_type": "deploy:production",
                "title": "Deploy v2.0",
                "description": "Full deployment",
            },
        )
        assert result.metadata["risk_level"] == "critical"

    async def test_with_classifier_unknown_defaults_to_high(
        self,
        tool_with_classifier: RequestHumanApprovalTool,
    ) -> None:
        result = await tool_with_classifier.execute(
            arguments={
                "action_type": "custom:unknown",
                "title": "Custom action",
                "description": "Unknown action type",
            },
        )
        assert result.metadata["risk_level"] == "high"


class TestValidation:
    """Action type format validation."""

    @pytest.mark.parametrize(
        "action_type",
        [
            "invalid",
            "no-colon",
            ":missing_category",
            "missing_action:",
            "too:many:colons",
            "  :  ",
        ],
    )
    async def test_invalid_action_type_rejected(
        self,
        tool: RequestHumanApprovalTool,
        action_type: str,
    ) -> None:
        result = await tool.execute(
            arguments={
                "action_type": action_type,
                "title": "Test",
                "description": "Test",
            },
        )
        assert result.is_error
        assert "category:action" in result.content

    async def test_valid_action_type_accepted(
        self,
        tool: RequestHumanApprovalTool,
    ) -> None:
        result = await tool.execute(
            arguments={
                "action_type": "deploy:production",
                "title": "Deploy",
                "description": "Deploy to prod",
            },
        )
        assert not result.is_error


class TestErrorHandling:
    """Graceful error handling on store failures."""

    async def test_store_error_returns_error_result(
        self,
        approval_store: ApprovalStore,
    ) -> None:
        tool = RequestHumanApprovalTool(
            approval_store=approval_store,
            agent_id="agent-1",
        )
        # First call succeeds
        result1 = await tool.execute(
            arguments={
                "action_type": "deploy:production",
                "title": "Deploy",
                "description": "Deploy to prod",
            },
        )
        assert not result1.is_error

        # Simulate store failure by monkeypatching
        async def _failing_add(item: object) -> None:
            msg = "Store unavailable"
            raise RuntimeError(msg)

        approval_store.add = _failing_add  # type: ignore[method-assign]

        result2 = await tool.execute(
            arguments={
                "action_type": "deploy:production",
                "title": "Deploy Again",
                "description": "Deploy to prod again",
            },
        )
        assert result2.is_error
        assert "Failed to create approval request" in result2.content


class TestRiskClassificationFailure:
    """Risk classifier exception handling."""

    async def test_classifier_exception_defaults_to_high(self) -> None:
        classifier = MagicMock(spec=DefaultRiskTierClassifier)
        classifier.classify.side_effect = ValueError("unexpected action")

        tool = RequestHumanApprovalTool(
            approval_store=ApprovalStore(),
            risk_classifier=classifier,
            agent_id="agent-1",
        )
        result = await tool.execute(
            arguments={
                "action_type": "custom:weird",
                "title": "Weird action",
                "description": "Unusual action",
            },
        )
        assert not result.is_error
        assert result.metadata["risk_level"] == ApprovalRiskLevel.HIGH.value

    async def test_classifier_returns_low_risk(self) -> None:
        classifier = MagicMock(spec=DefaultRiskTierClassifier)
        classifier.classify.return_value = ApprovalRiskLevel.LOW

        tool = RequestHumanApprovalTool(
            approval_store=ApprovalStore(),
            risk_classifier=classifier,
            agent_id="agent-1",
        )
        result = await tool.execute(
            arguments={
                "action_type": "read:config",
                "title": "Read config",
                "description": "Read configuration",
            },
        )
        assert not result.is_error
        assert result.metadata["risk_level"] == ApprovalRiskLevel.LOW.value
