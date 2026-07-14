"""Built-in role catalog and the reporting graph.

Provides the canonical set of built-in roles from the Agents design page
(Role Catalog). Authority follows each role's ``reports_to`` edge up to
the CEO (the single root); see :mod:`synthorg.core.authority` for the
graph queries built on top of this catalog.
"""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.core.normalization import normalize_identifier
from synthorg.core.role import Role
from synthorg.observability import get_logger
from synthorg.observability.events.role import ROLE_LOOKUP_MISS
from synthorg.organization.enums import DepartmentName

logger = get_logger(__name__)

# ── C-Suite / Executive ────────────────────────────────────────────

_CEO = Role(
    name="CEO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("strategy", "leadership", "communication"),
    reports_to=None,
    description=(
        "Overall strategy, final decision authority, cross-department coordination"
    ),
)

_CTO = Role(
    name="CTO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("architecture", "technology", "leadership"),
    reports_to="CEO",
    description="Technical vision, architecture decisions, technology choices",
)

_CFO = Role(
    name="CFO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("budgeting", "cost-optimization", "analytics"),
    reports_to="CEO",
    description="Budget management, cost optimization, resource allocation",
)

_COO = Role(
    name="COO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("operations", "process-optimization", "workflow"),
    reports_to="CEO",
    description="Operations, process optimization, workflow management",
)

_CPO = Role(
    name="CPO",
    department=DepartmentName.EXECUTIVE,
    required_skills=("product-strategy", "roadmap", "prioritization"),
    reports_to="CEO",
    description="Product strategy, roadmap, feature prioritization",
)

# ── Product & Design ───────────────────────────────────────────────

_PRODUCT_MANAGER = Role(
    name="Product Manager",
    department=DepartmentName.PRODUCT,
    required_skills=("requirements", "user-stories", "prioritization"),
    reports_to="CPO",
    description=(
        "Requirements, user stories, prioritization, stakeholder communication"
    ),
)

_UX_DESIGNER = Role(
    name="UX Designer",
    department=DepartmentName.DESIGN,
    required_skills=("user-research", "wireframes", "user-flows"),
    reports_to="CPO",
    description="User research, wireframes, user flows, usability",
)

_UI_DESIGNER = Role(
    name="UI Designer",
    department=DepartmentName.DESIGN,
    required_skills=("visual-design", "component-design", "design-systems"),
    reports_to="CPO",
    description="Visual design, component design, design systems",
)

_UX_RESEARCHER = Role(
    name="UX Researcher",
    department=DepartmentName.DESIGN,
    required_skills=("user-interviews", "analytics", "a-b-testing"),
    reports_to="CPO",
    description="User interviews, analytics, A/B test design",
)

_TECHNICAL_WRITER = Role(
    name="Technical Writer",
    department=DepartmentName.PRODUCT,
    required_skills=("documentation", "api-docs", "user-guides"),
    reports_to="Product Manager",
    description="Documentation, API docs, user guides",
)

# ── Engineering ────────────────────────────────────────────────────

_SOFTWARE_ARCHITECT = Role(
    name="Software Architect",
    department=DepartmentName.ENGINEERING,
    required_skills=("system-design", "architecture", "patterns"),
    reports_to="CTO",
    description="System design, technology decisions, patterns",
)

_FRONTEND_DEVELOPER = Role(
    name="Frontend Developer",
    department=DepartmentName.ENGINEERING,
    required_skills=("javascript", "css", "ui-frameworks"),
    reports_to="Software Architect",
    description="UI implementation, components, state management",
)

_BACKEND_DEVELOPER = Role(
    name="Backend Developer",
    department=DepartmentName.ENGINEERING,
    required_skills=("python", "apis", "databases"),
    reports_to="Software Architect",
    description="APIs, business logic, databases",
)

_FULLSTACK_DEVELOPER = Role(
    name="Full-Stack Developer",
    department=DepartmentName.ENGINEERING,
    required_skills=("javascript", "python", "databases"),
    reports_to="Software Architect",
    description="End-to-end implementation",
)

_DEVOPS_ENGINEER = Role(
    name="DevOps/SRE Engineer",
    department=DepartmentName.ENGINEERING,
    required_skills=("infrastructure", "ci-cd", "monitoring"),
    reports_to="Software Architect",
    description="Infrastructure, CI/CD, monitoring, deployment",
)

_DATABASE_ENGINEER = Role(
    name="Database Engineer",
    department=DepartmentName.ENGINEERING,
    required_skills=("schema-design", "query-optimization", "migrations"),
    reports_to="Software Architect",
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
    reports_to="CTO",
    description="Security audits, vulnerability assessment, secure coding",
)

# ── Quality Assurance ──────────────────────────────────────────────

_QA_LEAD = Role(
    name="QA Lead",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=("test-strategy", "quality-gates", "release-readiness"),
    reports_to="CTO",
    description="Test strategy, quality gates, release readiness",
)

_QA_ENGINEER = Role(
    name="QA Engineer",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=("test-plans", "manual-testing", "bug-reporting"),
    reports_to="QA Lead",
    description="Test plans, manual testing, bug reporting",
)

_AUTOMATION_ENGINEER = Role(
    name="Automation Engineer",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=("test-frameworks", "ci-integration", "e2e-testing"),
    reports_to="QA Lead",
    description="Test frameworks, CI integration, E2E tests",
)

_PERFORMANCE_ENGINEER = Role(
    name="Performance Engineer",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=("load-testing", "profiling", "optimization"),
    reports_to="QA Lead",
    description="Load testing, profiling, optimization",
)

# ── Data & Analytics ───────────────────────────────────────────────

_DATA_ANALYST = Role(
    name="Data Analyst",
    department=DepartmentName.DATA_ANALYTICS,
    required_skills=("metrics", "dashboards", "business-intelligence"),
    reports_to="CTO",
    description="Metrics, dashboards, business intelligence",
)

_DATA_ENGINEER = Role(
    name="Data Engineer",
    department=DepartmentName.DATA_ANALYTICS,
    required_skills=("pipelines", "etl", "data-infrastructure"),
    reports_to="CTO",
    description="Pipelines, ETL, data infrastructure",
)

_ML_ENGINEER = Role(
    name="ML Engineer",
    department=DepartmentName.DATA_ANALYTICS,
    required_skills=("model-training", "inference", "mlops"),
    reports_to="CTO",
    description="Model training, inference, MLOps",
)

# ── Operations & Support ──────────────────────────────────────────

_PROJECT_MANAGER = Role(
    name="Project Manager",
    department=DepartmentName.OPERATIONS,
    required_skills=("timelines", "dependencies", "risk-management"),
    reports_to="COO",
    description=("Timelines, dependencies, risk management, status tracking"),
)

_SCRUM_MASTER = Role(
    name="Scrum Master",
    department=DepartmentName.OPERATIONS,
    required_skills=("agile", "facilitation", "impediment-removal"),
    reports_to="COO",
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
    reports_to="COO",
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
    reports_to="CTO",
    description="Request validation, safety checks, approval workflows",
)

_RED_TEAM = Role(
    name="Red Team",
    department=DepartmentName.QUALITY_ASSURANCE,
    required_skills=(
        "adversarial-analysis",
        "claim-grounding",
        "security-review",
        "requirements-verification",
    ),
    reports_to="QA Lead",
    description=(
        "Built-in adversarial skeptic. Attacks every approved deliverable "
        "for correctness, security, unmet requirements, and ungrounded "
        "claims before the org marks the work complete."
    ),
)

RED_TEAM_ROLE_NAME: str = _RED_TEAM.name
"""Canonical name of the built-in Red Team role.

Exposed so other modules (the red-team subsystem, tests) reference a
single string constant instead of duplicating the literal.
"""

# ── Creative & Marketing ──────────────────────────────────────────

_BRAND_STRATEGIST = Role(
    name="Brand Strategist",
    department=DepartmentName.CREATIVE_MARKETING,
    required_skills=("messaging", "positioning", "competitive-analysis"),
    reports_to="CEO",
    description="Messaging, positioning, competitive analysis",
)

_CONTENT_WRITER = Role(
    name="Content Writer",
    department=DepartmentName.CREATIVE_MARKETING,
    required_skills=("blog-posts", "marketing-copy", "social-media"),
    reports_to="Brand Strategist",
    description="Blog posts, marketing copy, social media",
)

_GROWTH_MARKETER = Role(
    name="Growth Marketer",
    department=DepartmentName.CREATIVE_MARKETING,
    required_skills=("campaigns", "analytics", "conversion-optimization"),
    reports_to="Brand Strategist",
    description="Campaigns, analytics, conversion optimization",
)

# ── Knowledge Management ──────────────────────────────────────────

_KNOWLEDGE_ARCHITECT = Role(
    name="Knowledge Architect",
    department=DepartmentName.ENGINEERING,
    required_skills=("research", "synthesis", "writing"),
    reports_to="CTO",
    tool_access=(
        "memory.guide",
        "memory.search",
        "memory.read",
        "memory.write",
        "memory.delete",
        "memory.browse_wiki",
    ),
    description=(
        "Maintains organizational memory: curates wiki pages, "
        "synthesizes cross-project knowledge, maintains ADRs"
    ),
)

KNOWLEDGE_ARCHITECT_ROLE_NAME: str = _KNOWLEDGE_ARCHITECT.name
"""Canonical name of the built-in Knowledge Architect role.

Exposed so the org-memory write tools tag their author with a single
string constant instead of duplicating the literal.
"""

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
    _RED_TEAM,
    # Creative & Marketing
    _CONTENT_WRITER,
    _BRAND_STRATEGIST,
    _GROWTH_MARKETER,
    # Knowledge Management
    _KNOWLEDGE_ARCHITECT,
)


# ── Lookup Maps (built once at import time) ──────────────────────

_BUILTIN_ROLES_BY_NAME: Mapping[str, Role] = MappingProxyType(
    {normalize_identifier(r.name): r for r in BUILTIN_ROLES}
)
if len(_BUILTIN_ROLES_BY_NAME) != len(BUILTIN_ROLES):
    _msg = "Duplicate built-in role names after case-normalization"
    raise ValueError(_msg)

# Every non-root role must report to a role that exists in the catalog,
# so authority-graph walks always terminate at the CEO root.
for _role in BUILTIN_ROLES:
    if _role.reports_to is not None and (
        normalize_identifier(_role.reports_to) not in _BUILTIN_ROLES_BY_NAME
    ):
        _msg = (
            f"Role {_role.name!r} reports to unknown role "
            f"{_role.reports_to!r}; the catalog reporting graph is broken"
        )
        raise ValueError(_msg)
del _role


def get_builtin_role(name: str) -> Role | None:
    """Look up a built-in role by name (case-insensitive, whitespace-stripped).

    Args:
        name: Role name to search for.

    Returns:
        The matching Role, or ``None`` if not found.
    """
    result = _BUILTIN_ROLES_BY_NAME.get(normalize_identifier(name))
    if result is None:
        logger.debug(ROLE_LOOKUP_MISS, role_name=name)
    return result
