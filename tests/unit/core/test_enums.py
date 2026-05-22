"""Tests for core domain enumerations."""

import pytest

from synthorg.core.enums import (
    ActionType,
    AgentStatus,
    ArtifactType,
    CollaborationPreference,
    CommunicationVerbosity,
    CompanyType,
    Complexity,
    ConflictApproach,
    ConsolidationInterval,
    CostTier,
    CreativityLevel,
    DecisionMakingStyle,
    DepartmentName,
    MemoryCategory,
    MemoryLevel,
    Priority,
    ProjectStatus,
    RiskTolerance,
    SeniorityLevel,
    SkillPattern,
    TaskStatus,
    TaskType,
    WorkflowEdgeType,
    WorkflowNodeType,
    compare_seniority,
)

# ── Member Counts ──────────────────────────────────────────────────


@pytest.mark.unit
class TestEnumMemberCounts:
    def test_seniority_level_has_8_members(self) -> None:
        assert len(SeniorityLevel) == 8

    def test_agent_status_has_4_members(self) -> None:
        assert len(AgentStatus) == 4

    def test_risk_tolerance_has_3_members(self) -> None:
        assert len(RiskTolerance) == 3

    def test_creativity_level_has_3_members(self) -> None:
        assert len(CreativityLevel) == 3

    def test_memory_level_has_4_members(self) -> None:
        assert len(MemoryLevel) == 4

    def test_cost_tier_has_4_members(self) -> None:
        assert len(CostTier) == 4

    def test_company_type_has_10_members(self) -> None:
        assert len(CompanyType) == 10

    def test_department_name_has_9_members(self) -> None:
        assert len(DepartmentName) == 9

    def test_task_status_has_12_members(self) -> None:
        assert len(TaskStatus) == 12

    def test_task_type_has_6_members(self) -> None:
        assert len(TaskType) == 6

    def test_priority_has_4_members(self) -> None:
        assert len(Priority) == 4

    def test_complexity_has_4_members(self) -> None:
        assert len(Complexity) == 4

    def test_artifact_type_has_3_members(self) -> None:
        assert len(ArtifactType) == 3

    def test_project_status_has_5_members(self) -> None:
        assert len(ProjectStatus) == 5

    def test_decision_making_style_has_4_members(self) -> None:
        assert len(DecisionMakingStyle) == 4

    def test_collaboration_preference_has_3_members(self) -> None:
        assert len(CollaborationPreference) == 3

    def test_communication_verbosity_has_3_members(self) -> None:
        assert len(CommunicationVerbosity) == 3

    def test_conflict_approach_has_5_members(self) -> None:
        assert len(ConflictApproach) == 5

    def test_memory_category_has_7_members(self) -> None:
        # 5 agent categories + PROJECT_DOC + KNOWLEDGE.
        assert len(MemoryCategory) == 7
        assert MemoryCategory.PROJECT_DOC in set(MemoryCategory)
        assert MemoryCategory.PROJECT_DOC.value == "project_doc"
        assert MemoryCategory.KNOWLEDGE.value == "knowledge"

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

    def test_action_type_has_42_members(self) -> None:
        assert len(ActionType) == 42
        assert ActionType.MEMORY_READ.value == "memory:read"
        assert ActionType.KNOWLEDGE_INGEST.value == "knowledge:ingest"
        assert ActionType.KNOWLEDGE_REINDEX.value == "knowledge:reindex"
        assert ActionType.BROWSER_NAVIGATE.value == "browser:navigate"
        assert ActionType.EXTERNAL_DATA_REQUEST.value == "external_data:request"
        assert ActionType.DESKTOP_LAUNCH.value == "desktop:launch"
        assert ActionType.TOOL_CREATE.value == "tool:create"
        assert ActionType.RESEARCH_RUN.value == "research:run"


# ── String Values ──────────────────────────────────────────────────


@pytest.mark.unit
class TestEnumStringValues:
    def test_seniority_levels_are_lowercase(self) -> None:
        for member in SeniorityLevel:
            assert member.value == member.value.lower()

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

    def test_task_type_values(self) -> None:
        assert TaskType.DEVELOPMENT.value == "development"
        assert TaskType.DESIGN.value == "design"
        assert TaskType.RESEARCH.value == "research"
        assert TaskType.REVIEW.value == "review"
        assert TaskType.MEETING.value == "meeting"
        assert TaskType.ADMIN.value == "admin"

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
            (DecisionMakingStyle.ANALYTICAL, "analytical"),
            (DecisionMakingStyle.INTUITIVE, "intuitive"),
            (DecisionMakingStyle.CONSULTATIVE, "consultative"),
            (DecisionMakingStyle.DIRECTIVE, "directive"),
        ],
    )
    def test_decision_making_style_values(
        self, member: DecisionMakingStyle, value: str
    ) -> None:
        assert member.value == value

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (CollaborationPreference.INDEPENDENT, "independent"),
            (CollaborationPreference.PAIR, "pair"),
            (CollaborationPreference.TEAM, "team"),
        ],
    )
    def test_collaboration_preference_values(
        self, member: CollaborationPreference, value: str
    ) -> None:
        assert member.value == value

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (CommunicationVerbosity.TERSE, "terse"),
            (CommunicationVerbosity.BALANCED, "balanced"),
            (CommunicationVerbosity.VERBOSE, "verbose"),
        ],
    )
    def test_communication_verbosity_values(
        self, member: CommunicationVerbosity, value: str
    ) -> None:
        assert member.value == value

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (ConflictApproach.AVOID, "avoid"),
            (ConflictApproach.ACCOMMODATE, "accommodate"),
            (ConflictApproach.COMPETE, "compete"),
            (ConflictApproach.COMPROMISE, "compromise"),
            (ConflictApproach.COLLABORATE, "collaborate"),
        ],
    )
    def test_conflict_approach_values(
        self, member: ConflictApproach, value: str
    ) -> None:
        assert member.value == value

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
        assert isinstance(SeniorityLevel.JUNIOR, str)

    def test_strenum_equality_with_string(self) -> None:
        assert SeniorityLevel.JUNIOR == "junior"  # type: ignore[comparison-overlap]

    def test_strenum_iteration(self) -> None:
        levels = list(SeniorityLevel)
        assert len(levels) == 8
        assert levels[0] == SeniorityLevel.JUNIOR

    def test_strenum_membership(self) -> None:
        assert "senior" in SeniorityLevel.__members__.values()

    def test_strenum_from_value(self) -> None:
        assert SeniorityLevel("junior") is SeniorityLevel.JUNIOR

    def test_strenum_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError, match="not_a_level"):
            SeniorityLevel("not_a_level")


# ── Pydantic Integration ──────────────────────────────────────────


@pytest.mark.unit
class TestEnumPydanticIntegration:
    def test_enum_serializes_as_string(self) -> None:
        from pydantic import BaseModel

        class _M(BaseModel):
            level: SeniorityLevel

        m = _M(level=SeniorityLevel.SENIOR)
        dumped = m.model_dump()
        assert dumped["level"] == "senior"

    def test_enum_deserializes_from_string(self) -> None:
        from pydantic import BaseModel

        class _M(BaseModel):
            level: SeniorityLevel

        m = _M.model_validate({"level": "senior"})
        assert m.level is SeniorityLevel.SENIOR

    def test_enum_invalid_value_rejected(self) -> None:
        from pydantic import BaseModel, ValidationError

        class _M(BaseModel):
            level: SeniorityLevel

        with pytest.raises(ValidationError):
            _M.model_validate({"level": "invalid"})

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


# ── compare_seniority ────────────────────────────────────────────


@pytest.mark.unit
class TestCompareSeniority:
    def test_higher_is_positive(self) -> None:
        assert compare_seniority(SeniorityLevel.C_SUITE, SeniorityLevel.JUNIOR) > 0

    def test_lower_is_negative(self) -> None:
        assert compare_seniority(SeniorityLevel.JUNIOR, SeniorityLevel.SENIOR) < 0

    def test_equal_is_zero(self) -> None:
        assert compare_seniority(SeniorityLevel.LEAD, SeniorityLevel.LEAD) == 0

    def test_adjacent_levels(self) -> None:
        assert compare_seniority(SeniorityLevel.MID, SeniorityLevel.JUNIOR) > 0
        assert compare_seniority(SeniorityLevel.SENIOR, SeniorityLevel.MID) > 0

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            (SeniorityLevel.VP, SeniorityLevel.DIRECTOR),
            (SeniorityLevel.C_SUITE, SeniorityLevel.VP),
            (SeniorityLevel.PRINCIPAL, SeniorityLevel.LEAD),
        ],
    )
    def test_ordering_pairs(self, a: SeniorityLevel, b: SeniorityLevel) -> None:
        assert compare_seniority(a, b) > 0
        assert compare_seniority(b, a) < 0


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
