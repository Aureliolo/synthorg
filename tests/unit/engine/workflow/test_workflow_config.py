"""Tests for umbrella WorkflowConfig and integration points."""

import pytest
from pydantic import ValidationError

from synthorg.engine.workflow.config import WorkflowConfig
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.kanban_board import KanbanConfig
from synthorg.engine.workflow.sprint_config import SprintConfig

# ── WorkflowConfig ─────────────────────────────────────────────


class TestWorkflowConfig:
    """WorkflowConfig aggregates Kanban and Sprint configs."""

    @pytest.mark.unit
    def test_default_workflow_type(self) -> None:
        config = WorkflowConfig()
        assert config.workflow_type == WorkflowType.AGILE_KANBAN

    @pytest.mark.unit
    def test_default_kanban_config(self) -> None:
        config = WorkflowConfig()
        assert isinstance(config.kanban, KanbanConfig)
        assert len(config.kanban.wip_limits) == 2

    @pytest.mark.unit
    def test_default_sprint_config(self) -> None:
        config = WorkflowConfig()
        assert isinstance(config.sprint, SprintConfig)
        assert config.sprint.duration_days == 14

    @pytest.mark.unit
    def test_kanban_workflow_type(self) -> None:
        config = WorkflowConfig(
            workflow_type=WorkflowType.KANBAN,
        )
        assert config.workflow_type == WorkflowType.KANBAN

    @pytest.mark.unit
    def test_all_workflow_types_accepted(self) -> None:
        for wt in WorkflowType:
            config = WorkflowConfig(workflow_type=wt)
            assert config.workflow_type == wt

    @pytest.mark.unit
    def test_frozen(self) -> None:
        config = WorkflowConfig()
        with pytest.raises(ValidationError, match="frozen"):
            config.workflow_type = WorkflowType.KANBAN  # type: ignore[misc]


# ── WorkflowType enum ─────────────────────────────────────────


class TestWorkflowTypeEnum:
    """WorkflowType enum covers all design spec workflow types."""

    @pytest.mark.unit
    def test_member_count(self) -> None:
        assert len(WorkflowType) == 4

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (WorkflowType.SEQUENTIAL_PIPELINE, "sequential_pipeline"),
            (WorkflowType.PARALLEL_EXECUTION, "parallel_execution"),
            (WorkflowType.KANBAN, "kanban"),
            (WorkflowType.AGILE_KANBAN, "agile_kanban"),
        ],
    )
    def test_member_values(self, member: WorkflowType, value: str) -> None:
        assert member.value == value

    @pytest.mark.unit
    def test_string_coercion(self) -> None:
        """WorkflowType is a StrEnum -- string values coerce."""
        assert WorkflowType("kanban") is WorkflowType.KANBAN
        assert WorkflowType("agile_kanban") is WorkflowType.AGILE_KANBAN


# ── Template schema integration ────────────────────────────────


class TestTemplateSchemaIntegration:
    """CompanyTemplate.workflow accepts WorkflowType values."""

    @staticmethod
    def _template_data(**overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "metadata": {
                "name": "Test",
                "company_type": "custom",
            },
            "agents": [
                {"role": "Developer", "name": "test-dev"},
            ],
        }
        base.update(overrides)
        return base

    @pytest.mark.unit
    def test_template_workflow_field_accepts_kanban(self) -> None:
        from synthorg.templates.schema import CompanyTemplate

        template = CompanyTemplate.model_validate(
            self._template_data(workflow="kanban"),
        )
        assert template.workflow == WorkflowType.KANBAN

    @pytest.mark.unit
    def test_template_workflow_default_is_agile_kanban(self) -> None:
        from synthorg.templates.schema import CompanyTemplate

        template = CompanyTemplate.model_validate(self._template_data())
        assert template.workflow == WorkflowType.AGILE_KANBAN

    @pytest.mark.unit
    def test_template_rejects_invalid_workflow(self) -> None:
        from synthorg.templates.schema import CompanyTemplate

        with pytest.raises(ValidationError, match="workflow"):
            CompanyTemplate.model_validate(
                self._template_data(workflow="invalid_workflow"),
            )


# ── RootConfig integration ─────────────────────────────────────


class TestRootConfigIntegration:
    """RootConfig includes workflow configuration."""

    @pytest.mark.unit
    def test_root_config_has_workflow_field(self) -> None:
        from synthorg.config.schema import RootConfig

        config = RootConfig(company_name="Test Corp")
        assert isinstance(config.workflow, WorkflowConfig)
        assert config.workflow.workflow_type == WorkflowType.AGILE_KANBAN
