"""Unit tests for ``SubmitRedTeamReportTool``."""

import pytest

from synthorg.security.redteam.errors import RedTeamReportValidationError
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamSeverity,
)
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from synthorg.security.redteam.tools.submit_report import (
    SUBMIT_RED_TEAM_REPORT_TOOL_NAME,
    SubmitRedTeamReportTool,
)


def _high_finding() -> RedTeamFinding:
    return RedTeamFinding(
        attack_surface=RedTeamAttackSurface.SECURITY,
        severity=RedTeamSeverity.HIGH,
        description="Missing input length check",
        evidence=("L42: read input without length cap",),
    )


@pytest.fixture
def repo() -> InMemoryRedTeamReportRepository:
    return InMemoryRedTeamReportRepository()


@pytest.fixture
def tool(repo: InMemoryRedTeamReportRepository) -> SubmitRedTeamReportTool:
    return SubmitRedTeamReportTool(report_repo=repo)


@pytest.mark.unit
class TestToolMetadata:
    def test_canonical_name(self, tool: SubmitRedTeamReportTool) -> None:
        assert tool.name == SUBMIT_RED_TEAM_REPORT_TOOL_NAME

    def test_args_model_set(self, tool: SubmitRedTeamReportTool) -> None:
        assert tool.args_model is not None
        assert tool.args_model.__name__ == "SubmitRedTeamReportArgs"


@pytest.mark.unit
class TestToolExecute:
    """Tool writes the report through the repo on a valid payload."""

    @pytest.mark.asyncio
    async def test_valid_payload_persists_report(
        self,
        tool: SubmitRedTeamReportTool,
        repo: InMemoryRedTeamReportRepository,
    ) -> None:
        result = await tool.execute(
            arguments={
                "execution_id": "exec-1",
                "task_id": "task-1",
                "findings": (_high_finding().model_dump(mode="json"),),
                "summary": "One HIGH defect found.",
            },
        )
        assert result.is_error is False
        report = await repo.get(execution_id="exec-1")
        assert len(report.findings) == 1
        assert report.findings[0].severity is RedTeamSeverity.HIGH

    @pytest.mark.asyncio
    async def test_empty_findings_allowed_with_summary(
        self,
        tool: SubmitRedTeamReportTool,
        repo: InMemoryRedTeamReportRepository,
    ) -> None:
        result = await tool.execute(
            arguments={
                "execution_id": "exec-1",
                "task_id": "task-1",
                "summary": "Clean deliverable, no findings.",
            },
        )
        assert result.is_error is False
        report = await repo.get(execution_id="exec-1")
        assert report.findings == ()

    @pytest.mark.asyncio
    async def test_missing_summary_raises_validation_error(
        self,
        tool: SubmitRedTeamReportTool,
    ) -> None:
        with pytest.raises(RedTeamReportValidationError):
            await tool.execute(
                arguments={
                    "execution_id": "exec-1",
                    "task_id": "task-1",
                    "findings": (),
                },
            )

    @pytest.mark.asyncio
    async def test_missing_execution_id_raises_validation_error(
        self,
        tool: SubmitRedTeamReportTool,
    ) -> None:
        with pytest.raises(RedTeamReportValidationError):
            await tool.execute(
                arguments={"task_id": "task-1", "summary": "x"},
            )

    @pytest.mark.asyncio
    async def test_high_severity_finding_without_evidence_rejected(
        self,
        tool: SubmitRedTeamReportTool,
    ) -> None:
        """HIGH-severity findings without evidence fail model validation."""
        high_no_evidence = {
            "attack_surface": "security",
            "severity": "high",
            "description": "Missing input validation",
            "evidence": (),
        }
        with pytest.raises(RedTeamReportValidationError):
            await tool.execute(
                arguments={
                    "execution_id": "exec-1",
                    "task_id": "task-1",
                    "findings": (high_no_evidence,),
                    "summary": "HIGH defect without evidence.",
                },
            )

    @pytest.mark.asyncio
    async def test_unknown_field_raises_validation_error(
        self,
        tool: SubmitRedTeamReportTool,
    ) -> None:
        with pytest.raises(RedTeamReportValidationError):
            await tool.execute(
                arguments={
                    "execution_id": "exec-1",
                    "task_id": "task-1",
                    "summary": "x",
                    "bogus": True,
                },
            )

    @pytest.mark.asyncio
    async def test_duplicate_submission_returns_error_result(
        self,
        tool: SubmitRedTeamReportTool,
    ) -> None:
        first_args = {
            "execution_id": "exec-1",
            "task_id": "task-1",
            "summary": "first",
        }
        first = await tool.execute(arguments=first_args)
        assert first.is_error is False
        second = await tool.execute(
            arguments={**first_args, "summary": "second"},
        )
        assert second.is_error is True
        assert "single-shot" in second.content


@pytest.mark.unit
class TestExecutionIdArguments:
    """The tool reads execution_id and task_id from args, not constructor."""

    @pytest.mark.asyncio
    async def test_execution_id_is_in_args_schema(
        self,
        tool: SubmitRedTeamReportTool,
    ) -> None:
        schema = tool.parameters_schema
        assert schema is not None
        properties = schema.get("properties", {})
        assert "execution_id" in properties
        assert "task_id" in properties

    @pytest.mark.asyncio
    async def test_singleton_tool_serves_multiple_execution_ids(
        self,
        tool: SubmitRedTeamReportTool,
        repo: InMemoryRedTeamReportRepository,
    ) -> None:
        # One tool instance serves both writes. This is the production
        # pattern: the tool is registered once on the engine's registry.
        await tool.execute(
            arguments={
                "execution_id": "exec-A",
                "task_id": "task-A",
                "summary": "A clean",
            },
        )
        await tool.execute(
            arguments={
                "execution_id": "exec-B",
                "task_id": "task-B",
                "summary": "B clean",
            },
        )
        report_a = await repo.get(execution_id="exec-A")
        report_b = await repo.get(execution_id="exec-B")
        assert report_a.task_id == "task-A"
        assert report_b.task_id == "task-B"
