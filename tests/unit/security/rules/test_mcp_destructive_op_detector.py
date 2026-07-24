"""Tests for the destructive external-MCP operation detector."""

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.rules.mcp_destructive_op_detector import (
    MCPDestructiveOpDetector,
)


def _ctx(
    *,
    tool_name: str = "mcp_example_do_thing",
    category: ToolCategory = ToolCategory.MCP,
    arguments: dict[str, object] | None = None,
) -> SecurityContext:
    return SecurityContext(
        tool_name=tool_name,
        tool_category=category,
        action_type="comms:external",
        arguments=arguments or {},
    )


@pytest.mark.unit
class TestMCPDestructiveOpDetector:
    """Escalation of destructive external MCP operations."""

    def test_ignores_non_mcp_tools(self) -> None:
        """A destructive-looking non-MCP call is left to the shell detector."""
        detector = MCPDestructiveOpDetector()
        ctx = _ctx(
            tool_name="delete_local_file",
            category=ToolCategory.FILE_SYSTEM,
        )
        assert detector.evaluate(ctx) is None

    def test_ignores_benign_mcp_tools(self) -> None:
        """A read-only MCP call does not escalate."""
        detector = MCPDestructiveOpDetector()
        ctx = _ctx(tool_name="mcp_github_list_issues")
        assert detector.evaluate(ctx) is None

    @pytest.mark.parametrize(
        "tool_name",
        [
            "mcp_github_delete_repository",
            "mcp_slack_remove_channel",
            "mcp_infra_terminate_instance",
            "mcp_billing_revoke_license",
        ],
    )
    def test_destructive_tool_name_escalates_high(self, tool_name: str) -> None:
        """A destructive verb in the tool name escalates at HIGH."""
        detector = MCPDestructiveOpDetector()
        verdict = detector.evaluate(_ctx(tool_name=tool_name))
        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.ESCALATE
        assert verdict.risk_level == ApprovalRiskLevel.HIGH
        assert "mcp_destructive_op_detector" in verdict.matched_rules

    @pytest.mark.parametrize(
        "tool_name",
        [
            "mcp_infra_purge_bucket",
            "mcp_db_wipe_collection",
            "mcp_fleet_decommission_node",
        ],
    )
    def test_mass_destruction_escalates_critical(self, tool_name: str) -> None:
        """Mass-destruction verbs escalate at CRITICAL (still ESCALATE)."""
        detector = MCPDestructiveOpDetector()
        verdict = detector.evaluate(_ctx(tool_name=tool_name))
        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.ESCALATE
        assert verdict.risk_level == ApprovalRiskLevel.CRITICAL

    def test_destructive_dispatch_argument_escalates(self) -> None:
        """A single dispatch tool with a destructive action arg escalates."""
        detector = MCPDestructiveOpDetector()
        ctx = _ctx(
            tool_name="mcp_github_repos",
            arguments={"action": "delete_repo", "id": 12},
        )
        verdict = detector.evaluate(ctx)
        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.ESCALATE

    def test_deleted_substring_does_not_false_positive(self) -> None:
        """A non-verb token containing a verb substring does not match."""
        detector = MCPDestructiveOpDetector()
        ctx = _ctx(
            tool_name="mcp_audit_list_deleted",
            arguments={"filter": "undelete_history"},
        )
        assert detector.evaluate(ctx) is None

    @pytest.mark.parametrize(
        "tool_name",
        ["mcp_github_deleteRepository", "mcp_infra_purgeBucket"],
        ids=["delete", "purge"],
    )
    def test_camel_case_tool_name_escalates(self, tool_name: str) -> None:
        """An external server naming its tools in camelCase still escalates.

        Lower-casing before splitting would fuse ``deleteRepository`` into
        one unrecognised token, so a camelCase MCP server would bypass the
        whole detector.
        """
        detector = MCPDestructiveOpDetector()
        verdict = detector.evaluate(_ctx(tool_name=tool_name))
        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.ESCALATE

    def test_replace_argument_escalates(self) -> None:
        """Replacing upstream state destroys what was there before."""
        detector = MCPDestructiveOpDetector()
        ctx = _ctx(
            tool_name="mcp_github_repos",
            arguments={"action": "replace_branch_protection"},
        )
        verdict = detector.evaluate(ctx)
        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.ESCALATE
