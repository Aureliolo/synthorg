"""Built-in role catalog and seniority information.

Provides the canonical set of built-in roles from the Agents design page
(Role Catalog) and the seniority mapping (Seniority & Authority Levels).
"""

from synthorg.core.enums import (
    CostTier,
    DepartmentName,
    SeniorityLevel,
)
from synthorg.core.role import Role, SeniorityInfo
from synthorg.observability import get_logger
from synthorg.observability.events.role import ROLE_LOOKUP_MISS

logger = get_logger(__name__)

# ── Seniority Mapping ──────────────────────────────────────────────

SENIORITY_INFO: tuple[SeniorityInfo, ...] = (
    SeniorityInfo(
        level=SeniorityLevel.JUNIOR,
        authority_scope="Execute assigned tasks only",
        typical_model_tier="small",
        cost_tier=CostTier.LOW,
    ),
    SeniorityInfo(
        level=SeniorityLevel.MID,
        authority_scope="Execute and suggest improvements",
        typical_model_tier="medium",
        cost_tier=CostTier.MEDIUM,
    ),
    SeniorityInfo(
        level=SeniorityLevel.SENIOR,
        authority_scope="Execute, design, and review others",
        typical_model_tier="medium",
        cost_tier=CostTier.HIGH,
    ),
    SeniorityInfo(
        level=SeniorityLevel.LEAD,
        authority_scope="All above plus approve and delegate",
        typical_model_tier="large",
        cost_tier=CostTier.HIGH,
    ),
    SeniorityInfo(
        level=SeniorityLevel.PRINCIPAL,
        authority_scope="All above plus architectural decisions",
        typical_model_tier="large",
        cost_tier=CostTier.PREMIUM,
    ),
    SeniorityInfo(
        level=SeniorityLevel.DIRECTOR,
        authority_scope="Strategic decisions and budget authority",
        typical_model_tier="large",
        cost_tier=CostTier.PREMIUM,
    ),
    SeniorityInfo(
        level=SeniorityLevel.VP,
        authority_scope="Department-wide authority",
        typical_model_tier="large",
        cost_tier=CostTier.PREMIUM,
    ),
    SeniorityInfo(
        level=SeniorityLevel.C_SUITE,
        authority_scope="Company-wide authority and final approvals",
        typical_model_tier="large",
        cost_tier=CostTier.PREMIUM,
    ),
)

# ── C-Suite / Executive ────────────────────────────────────────────

_CEO = Role(
    name="CEO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("strategy", "leadership", "communication"),
    authority_level=SeniorityLevel.C_SUITE,
    description=(
        "Overall strategy, final decision authority, cross-department coordination"
    ),
)

_CTO = Role(
    name="CTO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("architecture", "technology", "leadership"),
    authority_level=SeniorityLevel.C_SUITE,
    description="Technical vision, architecture decisions, technology choices",
)

_CFO = Role(
    name="CFO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("budgeting", "cost-optimization", "analytics"),
    authority_level=SeniorityLevel.C_SUITE,
    description="Budget management, cost optimization, resource allocation",
)

_COO = Role(
    name="COO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("operations", "process-optimization", "workflow"),
    authority_level=SeniorityLevel.C_SUITE,
    description="Operations, process optimization, workflow management",
)

_CPO = Role(
    name="CPO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("product-strategy", "roadmap", "prioritization"),
    authority_level=SeniorityLevel.C_SUITE,
    description="Product strategy, roadmap, feature prioritization",
)

# ── Product & Design ───────────────────────────────────────────────

_PRODUCT_MANAGER = Role(
    name="Product Manager",
    department=DepartmentName.PRODUCT,
    required_skills=("requirements", "user-stories", "prioritization"),
    authority_level=SeniorityLevel.SENIOR,
    description=(
        "Requirements, user stories, prioritization, stakeholder communication"
    ),
)

_UX_DESIGNER = Role(
    name="UX Designer",
    department=DepartmentName.DESIGN,
    required_skills=("user-research", "wireframes", "user-flows"),
    authority_level=SeniorityLevel.MID,
    description="User research, wireframes, user flows, usability",
)

_UI_DESIGNER = Role(
    name="UI Designer",
    department=DepartmentName.DESIGN,
    required_skills=("visual-design", "component-design", "design-systems"),
    authority_level=SeniorityLevel.MID,
    description="Visual design, component design, design systems",
)

_UX_RESEARCHER = Role(
    name="UX Researcher",
    department=DepartmentName.DESIGN,
    required_skills=("user-interviews", "analytics", "a-b-testing"),
    authority_level=SeniorityLevel.MID,
    description="User interviews, analytics, A/B test design",
)

_TECHNICAL_WRITER = Role(
    name="Technical Writer",
    department=DepartmentName.PRODUCT,
    required_skills=("documentation", "api-docs", "user-guides"),
    authority_level=SeniorityLevel.MID,
    description="Documentation, API docs, user guides",
)

# ── Engineering ────────────────────────────────────────────────────

_SOFTWARE_ARCHITECT = Role(
    name="Software Architect",
    department=DepartmentName.ENGINEERING,
    required_skills=("system-design", "architecture", "patterns"),
    authority_level=SeniorityLevel.PRINCIPAL,
    description="System design, technology decisions, patterns",
)

_FRONTEND_DEVELOPER = Role(
    name="Frontend Developer",
    department=DepartmentName.ENGINEERING,
    required_skills=("javascript", "css", "ui-frameworks"),
    authority_level=SeniorityLevel.MID,
    description="UI implementation, components, state management",
)

_BACKEND_DEVELOPER = Role(
    name="Backend Developer",
    department=DepartmentName.ENGINEERING,
    required_skills=("python", "apis", "databases"),
    authority_level=SeniorityLevel.MID,
    description="APIs, business logic, databases",
)

_FULLSTACK_DEVELOPER = Role(
    name="Full-Stack Developer",
    department=DepartmentName.ENGINEERING,
    required_skills=("javascript", "python", "databases"),
    authority_level=SeniorityLevel.MID,
    description="End-to-end implementation",
)

_DEVOPS_ENGINEER = Role(
    name="DevOps/SRE Engineer",
    department=DepartmentName.ENGINEERING,
    required_skills=("infrastructure", "ci-cd", "monitoring"),
    authority_level=SeniorityLevel.MID,
    description="Infrastructure, CI/CD, monitoring, deployment",
)

_DATABASE_ENGINEER = Role(
    name="Database Engineer",
    department=DepartmentName.ENGINEERING,
    required_skills=("schema-design", "query-optimization", "migrations"),
    authority_level=SeniorityLevel.MID,
    description="Schema design, query optimization, migrations",
)

_SECURITY_ENGINEER = Role(
    name="Security Engineer",
    department=DepartmentName.SECURITY,
    required_skills=(
        "security-audits",
        "vulnerability-assessment",
        "secure-coding",
    ),
    authority_level=SeniorityLevel.SENIOR,
    description="Security audits, vulnerability assessment, secure coding",
)

# ── Quality Assurance ──────────────────────────────────────────────

_QA_LEAD = Role(
    name="QA Lead",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=("test-strategy", "quality-gates", "release-readiness"),
    authority_level=SeniorityLevel.LEAD,
    description="Test strategy, quality gates, release readiness",
)

_QA_ENGINEER = Role(
    name="QA Engineer",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=("test-plans", "manual-testing", "bug-reporting"),
    authority_level=SeniorityLevel.MID,
    description="Test plans, manual testing, bug reporting",
)

_AUTOMATION_ENGINEER = Role(
    name="Automation Engineer",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=("test-frameworks", "ci-integration", "e2e-testing"),
    authority_level=SeniorityLevel.MID,
    description="Test frameworks, CI integration, E2E tests",
)

_PERFORMANCE_ENGINEER = Role(
    name="Performance Engineer",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=("load-testing", "profiling", "optimization"),
    authority_level=SeniorityLevel.SENIOR,
    description="Load testing, profiling, optimization",
)

# ── Data & Analytics ───────────────────────────────────────────────

_DATA_ANALYST = Role(
    name="Data Analyst",
    department=DepartmentName.DATA_ANALYTICS,
    required_skills=("metrics", "dashboards", "business-intelligence"),
    authority_level=SeniorityLevel.MID,
    description="Metrics, dashboards, business intelligence",
)

_DATA_ENGINEER = Role(
    name="Data Engineer",
    department=DepartmentName.DATA_ANALYTICS,
    required_skills=("pipelines", "etl", "data-infrastructure"),
    authority_level=SeniorityLevel.MID,
    description="Pipelines, ETL, data infrastructure",
)

_ML_ENGINEER = Role(
    name="ML Engineer",
    department=DepartmentName.DATA_ANALYTICS,
    required_skills=("model-training", "inference", "mlops"),
    authority_level=SeniorityLevel.SENIOR,
    description="Model training, inference, MLOps",
)

# ── Operations & Support ──────────────────────────────────────────

_PROJECT_MANAGER = Role(
    name="Project Manager",
    department=DepartmentName.OPERATIONS,
    required_skills=("timelines", "dependencies", "risk-management"),
    authority_level=SeniorityLevel.SENIOR,
    description=("Timelines, dependencies, risk management, status tracking"),
)

_SCRUM_MASTER = Role(
    name="Scrum Master",
    department=DepartmentName.OPERATIONS,
    required_skills=("agile", "facilitation", "impediment-removal"),
    authority_level=SeniorityLevel.SENIOR,
    description="Agile ceremonies, impediment removal, team health",
)

_HR_MANAGER = Role(
    name="HR Manager",
    department=DepartmentName.OPERATIONS,
    required_skills=(
        "hiring",
        "team-composition",
        "performance-tracking",
    ),
    authority_level=SeniorityLevel.SENIOR,
    description=("Hiring recommendations, team composition, performance tracking"),
)

_SECURITY_OPERATIONS = Role(
    name="Security Operations",
    department=DepartmentName.SECURITY,
    required_skills=(
        "request-validation",
        "safety-checks",
        "approval-workflows",
    ),
    authority_level=SeniorityLevel.SENIOR,
    description="Request validation, safety checks, approval workflows",
)

# ── Creative & Marketing ──────────────────────────────────────────

_CONTENT_WRITER = Role(
    name="Content Writer",
    department=DepartmentName.CREATIVE_MARKETING,
    required_skills=("blog-posts", "marketing-copy", "social-media"),
    authority_level=SeniorityLevel.MID,
    description="Blog posts, marketing copy, social media",
)

_BRAND_STRATEGIST = Role(
    name="Brand Strategist",
    department=DepartmentName.CREATIVE_MARKETING,
    required_skills=("messaging", "positioning", "competitive-analysis"),
    authority_level=SeniorityLevel.SENIOR,
    description="Messaging, positioning, competitive analysis",
)

_GROWTH_MARKETER = Role(
    name="Growth Marketer",
    department=DepartmentName.CREATIVE_MARKETING,
    required_skills=("campaigns", "analytics", "conversion-optimization"),
    authority_level=SeniorityLevel.MID,
    description="Campaigns, analytics, conversion optimization",
)

# ── Aggregated Catalog ─────────────────────────────────────────────

BUILTIN_ROLES: tuple[Role, ...] = (
    # C-Suite
    _CEO,
    _CTO,
    _CFO,
    _COO,
    _CPO,
    # Product & Design
    _PRODUCT_MANAGER,
    _UX_DESIGNER,
    _UI_DESIGNER,
    _UX_RESEARCHER,
    _TECHNICAL_WRITER,
    # Engineering
    _SOFTWARE_ARCHITECT,
    _FRONTEND_DEVELOPER,
    _BACKEND_DEVELOPER,
    _FULLSTACK_DEVELOPER,
    _DEVOPS_ENGINEER,
    _DATABASE_ENGINEER,
    _SECURITY_ENGINEER,
    # Quality Assurance
    _QA_LEAD,
    _QA_ENGINEER,
    _AUTOMATION_ENGINEER,
    _PERFORMANCE_ENGINEER,
    # Data & Analytics
    _DATA_ANALYST,
    _DATA_ENGINEER,
    _ML_ENGINEER,
    # Operations & Support
    _PROJECT_MANAGER,
    _SCRUM_MASTER,
    _HR_MANAGER,
    _SECURITY_OPERATIONS,
    # Creative & Marketing
    _CONTENT_WRITER,
    _BRAND_STRATEGIST,
    _GROWTH_MARKETER,
)


# ── Lookup Maps (built once at import time) ──────────────────────

_BUILTIN_ROLES_BY_NAME: dict[str, Role] = {r.name.casefold(): r for r in BUILTIN_ROLES}
if len(_BUILTIN_ROLES_BY_NAME) != len(BUILTIN_ROLES):
    _msg = "Duplicate built-in role names after case-normalization"
    raise ValueError(_msg)

_SENIORITY_INFO_BY_LEVEL: dict[SeniorityLevel, SeniorityInfo] = {
    info.level: info for info in SENIORITY_INFO
}
if len(_SENIORITY_INFO_BY_LEVEL) != len(SENIORITY_INFO):
    _msg = "Duplicate seniority levels found in SENIORITY_INFO"
    raise ValueError(_msg)

_missing_levels = set(SeniorityLevel) - set(_SENIORITY_INFO_BY_LEVEL)
if _missing_levels:
    _msg = f"Missing seniority mappings: {sorted(lv.value for lv in _missing_levels)}"
    raise ValueError(_msg)


def get_builtin_role(name: str) -> Role | None:
    """Look up a built-in role by name (case-insensitive, whitespace-stripped).

    Args:
        name: Role name to search for.

    Returns:
        The matching Role, or ``None`` if not found.
    """
    result = _BUILTIN_ROLES_BY_NAME.get(name.strip().casefold())
    if result is None:
        logger.debug(ROLE_LOOKUP_MISS, role_name=name)
    return result


def get_seniority_info(level: SeniorityLevel) -> SeniorityInfo:
    """Look up seniority info by level.

    Args:
        level: The seniority level to look up.

    Returns:
        The matching SeniorityInfo.

    Raises:
        LookupError: If no entry exists for the given level.
    """
    info = _SENIORITY_INFO_BY_LEVEL.get(level)
    if info is None:
        logger.warning(
            ROLE_LOOKUP_MISS,
            level=level.value,
            reason="no seniority info in catalog",
        )
        msg = f"No seniority info for level {level!r}; catalog may be incomplete"
        raise LookupError(msg)
    return info
