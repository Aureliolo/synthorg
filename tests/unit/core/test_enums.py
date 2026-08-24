"""Tests for core domain enumerations."""

import pytest

from synthorg.core.artifact import ArtifactType
from synthorg.core.memory_enums import MemoryCategory, MemoryLevel
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.workflow.enums import WorkflowEdgeType, WorkflowNodeType
from synthorg.hr.enums import AgentStatus, CostTier
from synthorg.memory.enums import ConsolidationInterval
from synthorg.organization.enums import CompanyType, DepartmentName
from synthorg.security.autonomy.enums import ActionType
from synthorg.templates.enums import SkillPattern

# ── Member Counts ──────────────────────────────────────────────────


@pytest.mark.unit
class TestEnumMemberCounts:
    def test_agent_status_has_4_members(self) -> None:
        assert len(AgentStatus) == 4

    def test_memory_level_has_4_members(self) -> None:
        assert len(MemoryLevel) == 4

    def test_cost_tier_has_4_members(self) -> None:
        assert len(CostTier) == 4

    def test_company_type_has_13_members(self) -> None:
        assert len(CompanyType) == 13

    def test_department_name_has_9_members(self) -> None:
        assert len(DepartmentName) == 9

    def test_task_status_has_13_members(self) -> None:
        assert len(TaskStatus) == 13

    def test_task_type_has_7_members(self) -> None:
        assert len(TaskType) == 7

    def test_priority_has_4_members(self) -> None:
        assert len(Priority) == 4

    def test_complexity_has_4_members(self) -> None:
        assert len(Complexity) == 4

    def test_artifact_type_has_3_members(self) -> None:
        assert len(ArtifactType) == 3

    def test_project_status_has_7_members(self) -> None:
        assert len(ProjectStatus) == 7

    def test_memory_category_has_8_members(self) -> None:
        # 5 agent categories + PROJECT_DOC + KNOWLEDGE + PROJECT_BRAIN.
        assert len(MemoryCategory) == 8
        assert MemoryCategory.PROJECT_DOC in set(MemoryCategory)
        assert MemoryCategory.PROJECT_DOC.value == "project_doc"
        assert MemoryCategory.KNOWLEDGE.value == "knowledge"
        assert MemoryCategory.PROJECT_BRAIN.value == "project_brain"

    def test_consolidation_interval_has_4_members(self) -> None:
        assert len(ConsolidationInterval) == 4

    def test_skill_pattern_has_5_members(self) -> None:
        assert len(SkillPattern) == 5

    def test_skill_pattern_values(self) -> None:
        expected = {
            "tool_wrapper",
            "generator",
            "reviewer",
            "inversion",
            "pipeline",
        }
        assert {sp.value for sp in SkillPattern} == expected

    def test_workflow_node_type_has_9_members(self) -> None:
        assert len(WorkflowNodeType) == 9

    def test_workflow_edge_type_has_7_members(self) -> None:
        assert len(WorkflowEdgeType) == 7

    def test_action_type_has_47_members(self) -> None:
        assert len(ActionType) == 47
        assert ActionType.DESIGN_GENERATE.value == "design:generate"
        assert ActionType.DESIGN_DELETE.value == "design:delete"
        assert ActionType.PUBLISH_STAGING.value == "publish:staging"
        assert ActionType.PUBLISH_PRODUCTION.value == "publish:production"
        assert ActionType.MEMORY_READ.value == "memory:read"
        assert ActionType.KNOWLEDGE_INGEST.value == "knowledge:ingest"
        assert ActionType.KNOWLEDGE_REINDEX.value == "knowledge:reindex"
        assert ActionType.BROWSER_NAVIGATE.value == "browser:navigate"
        assert ActionType.EXTERNAL_DATA_REQUEST.value == "external_data:request"
        assert ActionType.DESKTOP_LAUNCH.value == "desktop:launch"
        assert ActionType.TOOL_CREATE.value == "tool:create"
        assert ActionType.RESEARCH_RUN.value == "research:run"
        assert ActionType.ORG_DELEGATE.value == "org:delegate"


# ── String Values ──────────────────────────────────────────────────


@pytest.mark.unit
class TestEnumStringValues:
    def test_agent_status_values(self) -> None:
        assert AgentStatus.ACTIVE.value == "active"
        assert AgentStatus.ON_LEAVE.value == "on_leave"
        assert AgentStatus.TERMINATED.value == "terminated"
        assert AgentStatus.ONBOARDING.value == "onboarding"

    def test_cost_tier_values(self) -> None:
        assert CostTier.LOW.value == "low"
        assert CostTier.MEDIUM.value == "medium"
        assert CostTier.HIGH.value == "high"
        assert CostTier.PREMIUM.value == "premium"

    def test_company_type_values(self) -> None:
        assert CompanyType.SOLO_FOUNDER.value == "solo_founder"
        assert CompanyType.STARTUP.value == "startup"
        assert CompanyType.CONSULTANCY.value == "consultancy"
        assert CompanyType.DATA_TEAM.value == "data_team"
        assert CompanyType.CUSTOM.value == "custom"

    def test_task_status_values(self) -> None:
        assert TaskStatus.CREATED.value == "created"
        assert TaskStatus.ASSIGNED.value == "assigned"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.IN_REVIEW.value == "in_review"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.BLOCKED.value == "blocked"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"
        assert TaskStatus.INTERRUPTED.value == "interrupted"
        assert TaskStatus.SUSPENDED.value == "suspended"
        assert TaskStatus.REJECTED.value == "rejected"
        assert TaskStatus.AUTH_REQUIRED.value == "auth_required"
        assert TaskStatus.AWAITING_INPUT.value == "awaiting_input"

    def test_task_type_values(self) -> None:
        assert TaskType.DEVELOPMENT.value == "development"
        assert TaskType.DESIGN.value == "design"
        assert TaskType.RESEARCH.value == "research"
        assert TaskType.REVIEW.value == "review"
        assert TaskType.MEETING.value == "meeting"
        assert TaskType.ADMIN.value == "admin"
        assert TaskType.ANALYSIS.value == "analysis"

    def test_priority_values(self) -> None:
        assert Priority.CRITICAL.value == "critical"
        assert Priority.HIGH.value == "high"
        assert Priority.MEDIUM.value == "medium"
        assert Priority.LOW.value == "low"

    def test_complexity_values(self) -> None:
        assert Complexity.SIMPLE.value == "simple"
        assert Complexity.MEDIUM.value == "medium"
        assert Complexity.COMPLEX.value == "complex"
        assert Complexity.EPIC.value == "epic"

    def test_artifact_type_values(self) -> None:
        assert ArtifactType.CODE.value == "code"
        assert ArtifactType.TESTS.value == "tests"
        assert ArtifactType.DOCUMENTATION.value == "documentation"

    def test_project_status_values(self) -> None:
        assert ProjectStatus.PLANNING.value == "planning"
        assert ProjectStatus.ACTIVE.value == "active"
        assert ProjectStatus.ON_HOLD.value == "on_hold"
        assert ProjectStatus.COMPLETED.value == "completed"
        assert ProjectStatus.CANCELLED.value == "cancelled"

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (MemoryCategory.WORKING, "working"),
            (MemoryCategory.EPISODIC, "episodic"),
            (MemoryCategory.SEMANTIC, "semantic"),
            (MemoryCategory.PROCEDURAL, "procedural"),
            (MemoryCategory.SOCIAL, "social"),
        ],
    )
    def test_memory_category_values(self, member: MemoryCategory, value: str) -> None:
        assert member.value == value

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (ConsolidationInterval.HOURLY, "hourly"),
            (ConsolidationInterval.DAILY, "daily"),
            (ConsolidationInterval.WEEKLY, "weekly"),
            (ConsolidationInterval.NEVER, "never"),
        ],
    )
    def test_consolidation_interval_values(
        self, member: ConsolidationInterval, value: str
    ) -> None:
        assert member.value == value

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (ActionType.CODE_READ, "code:read"),
            (ActionType.CODE_WRITE, "code:write"),
            (ActionType.CODE_DELETE, "code:delete"),
            (ActionType.VCS_COMMIT, "vcs:commit"),
            (ActionType.VCS_PUSH, "vcs:push"),
            (ActionType.DEPLOY_PRODUCTION, "deploy:production"),
            (ActionType.BUDGET_SPEND, "budget:spend"),
            (ActionType.ORG_FIRE, "org:fire"),
            (ActionType.DB_ADMIN, "db:admin"),
            (ActionType.ARCH_DECIDE, "arch:decide"),
        ],
    )
    def test_action_type_values(self, member: ActionType, value: str) -> None:
        assert member.value == value

    def test_workflow_node_type_verification_value(self) -> None:
        assert WorkflowNodeType.VERIFICATION.value == "verification"

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (WorkflowEdgeType.VERIFICATION_PASS, "verification_pass"),
            (WorkflowEdgeType.VERIFICATION_FAIL, "verification_fail"),
            (WorkflowEdgeType.VERIFICATION_REFER, "verification_refer"),
        ],
    )
    def test_workflow_edge_type_verification_values(
        self, member: WorkflowEdgeType, value: str
    ) -> None:
        assert member.value == value

    def test_action_type_uses_colon_format(self) -> None:
        for member in ActionType:
            assert ":" in member.value, f"{member.name} lacks category:action format"


# ── StrEnum Behavior ───────────────────────────────────────────────


@pytest.mark.unit
class TestStrEnumBehavior:
    def test_strenum_is_string(self) -> None:
        assert isinstance(AgentStatus.ACTIVE, str)

    def test_strenum_equality_with_string(self) -> None:
        assert AgentStatus.ACTIVE == "active"  # type: ignore[comparison-overlap]

    def test_strenum_iteration(self) -> None:
        statuses = list(AgentStatus)
        assert len(statuses) == 4
        assert statuses[0] == AgentStatus.ACTIVE

    def test_strenum_membership(self) -> None:
        assert "terminated" in AgentStatus.__members__.values()

    def test_strenum_from_value(self) -> None:
        assert AgentStatus("active") is AgentStatus.ACTIVE

    def test_strenum_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError, match="not_a_status"):
            AgentStatus("not_a_status")


# ── Pydantic Integration ──────────────────────────────────────────


@pytest.mark.unit
class TestEnumPydanticIntegration:
    def test_enum_serializes_as_string(self) -> None:
        from pydantic import BaseModel

        class _M(BaseModel):
            status: AgentStatus

        m = _M(status=AgentStatus.ACTIVE)
        dumped = m.model_dump()
        assert dumped["status"] == "active"

    def test_enum_deserializes_from_string(self) -> None:
        from pydantic import BaseModel

        class _M(BaseModel):
            status: AgentStatus

        m = _M.model_validate({"status": "active"})
        assert m.status is AgentStatus.ACTIVE

    def test_enum_invalid_value_rejected(self) -> None:
        from pydantic import BaseModel, ValidationError

        class _M(BaseModel):
            status: AgentStatus

        with pytest.raises(ValidationError):
            _M.model_validate({"status": "invalid"})

    def test_enum_json_roundtrip(self) -> None:
        from pydantic import BaseModel

        class _M(BaseModel):
            status: AgentStatus
            tier: CostTier

        m = _M(status=AgentStatus.ACTIVE, tier=CostTier.PREMIUM)
        json_str = m.model_dump_json()
        restored = _M.model_validate_json(json_str)
        assert restored.status is AgentStatus.ACTIVE
        assert restored.tier is CostTier.PREMIUM


# ── Package layout ───────────────────────────────────────────────


@pytest.mark.unit
class TestCoreLayout:
    def test_core_package_is_lean(self) -> None:
        """``synthorg.core`` must not eagerly import submodules.

        Historically ``synthorg.core/__init__.py`` eagerly re-exported
        everything from agent, company, tools, etc., adding ~2s to
        every import path that needed a leaf type from
        ``synthorg.core.types``.  Keep the package ``__init__`` lean
        and require callers to import from the defining submodule.
        """
        import synthorg.core as core_module

        # Must not define __all__ (no re-exports) and must not have
        # heavy submodule attributes attached at package init.
        assert not hasattr(core_module, "__all__")
        assert not hasattr(core_module, "AgentIdentity")
        assert not hasattr(core_module, "Company")
