"""Core domain models for the AI company framework."""

from ai_company.core.agent import (
    AgentIdentity,
    MemoryConfig,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
    ToolPermissions,
)
from ai_company.core.artifact import Artifact, ExpectedArtifact
from ai_company.core.company import (
    Company,
    CompanyConfig,
    Department,
    HRRegistry,
    Team,
)
from ai_company.core.enums import (
    AgentStatus,
    ArtifactType,
    CompanyType,
    Complexity,
    CostTier,
    CreativityLevel,
    DepartmentName,
    MemoryType,
    Priority,
    ProficiencyLevel,
    ProjectStatus,
    RiskTolerance,
    SeniorityLevel,
    SkillCategory,
    TaskStatus,
    TaskType,
)
from ai_company.core.project import Project
from ai_company.core.role import (
    Authority,
    CustomRole,
    Role,
    SeniorityInfo,
    Skill,
)
from ai_company.core.role_catalog import (
    BUILTIN_ROLES,
    SENIORITY_INFO,
    get_builtin_role,
    get_seniority_info,
)
from ai_company.core.task import AcceptanceCriterion, Task
from ai_company.core.task_transitions import VALID_TRANSITIONS, validate_transition
from ai_company.core.types import NotBlankStr, validate_unique_strings

__all__ = [
    "BUILTIN_ROLES",
    "SENIORITY_INFO",
    "VALID_TRANSITIONS",
    "AcceptanceCriterion",
    "AgentIdentity",
    "AgentStatus",
    "Artifact",
    "ArtifactType",
    "Authority",
    "Company",
    "CompanyConfig",
    "CompanyType",
    "Complexity",
    "CostTier",
    "CreativityLevel",
    "CustomRole",
    "Department",
    "DepartmentName",
    "ExpectedArtifact",
    "HRRegistry",
    "MemoryConfig",
    "MemoryType",
    "ModelConfig",
    "NotBlankStr",
    "PersonalityConfig",
    "Priority",
    "ProficiencyLevel",
    "Project",
    "ProjectStatus",
    "RiskTolerance",
    "Role",
    "SeniorityInfo",
    "SeniorityLevel",
    "Skill",
    "SkillCategory",
    "SkillSet",
    "Task",
    "TaskStatus",
    "TaskType",
    "Team",
    "ToolPermissions",
    "get_builtin_role",
    "get_seniority_info",
    "validate_transition",
    "validate_unique_strings",
]
