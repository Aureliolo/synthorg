"""Security action, tool-category, and autonomy-downgrade enumerations."""

from enum import StrEnum


class ToolCategory(StrEnum):
    """Category of a tool for access-level gating."""

    FILE_SYSTEM = "file_system"
    CODE_EXECUTION = "code_execution"
    VERSION_CONTROL = "version_control"
    WEB = "web"
    DATABASE = "database"
    TERMINAL = "terminal"
    DESIGN = "design"
    COMMUNICATION = "communication"
    ANALYTICS = "analytics"
    DEPLOYMENT = "deployment"
    MEMORY = "memory"
    ONTOLOGY = "ontology"
    MCP = "mcp"
    BROWSER = "browser"
    EXTERNAL_DATA = "external_data"
    DESKTOP = "desktop"
    OTHER = "other"


class ActionType(StrEnum):
    """Two-level action type taxonomy for security classification.

    Used by autonomy presets (see ``docs/design/security.md``), SecOps
    validation, and tiered timeout policies. Values follow a
    ``category:action`` naming convention.

    Custom action type strings are also accepted by models that use
    ``str`` for ``action_type`` fields -- these enum members are
    convenience constants for the built-in taxonomy.
    """

    CODE_READ = "code:read"
    CODE_WRITE = "code:write"
    CODE_CREATE = "code:create"
    CODE_DELETE = "code:delete"
    CODE_REFACTOR = "code:refactor"
    TEST_WRITE = "test:write"
    TEST_RUN = "test:run"
    DOCS_WRITE = "docs:write"
    # Separate from docs:write because a design tool leaves the worktree:
    # generation calls an external provider and is billed, and the asset
    # store outlives the run that wrote it. Sharing docs:write put both
    # behind a type the risk map scores LOW and the supervised preset
    # auto-approves.
    DESIGN_GENERATE = "design:generate"
    DESIGN_DELETE = "design:delete"
    VCS_COMMIT = "vcs:commit"
    VCS_PUSH = "vcs:push"
    VCS_BRANCH = "vcs:branch"
    DEPLOY_STAGING = "deploy:staging"
    DEPLOY_PRODUCTION = "deploy:production"
    PUBLISH_STAGING = "publish:staging"
    PUBLISH_PRODUCTION = "publish:production"
    COMMS_INTERNAL = "comms:internal"
    COMMS_EXTERNAL = "comms:external"
    BUDGET_SPEND = "budget:spend"
    BUDGET_EXCEED = "budget:exceed"
    ORG_HIRE = "org:hire"
    ORG_FIRE = "org:fire"
    ORG_PROMOTE = "org:promote"
    ORG_DELEGATE = "org:delegate"
    VCS_READ = "vcs:read"
    DB_QUERY = "db:query"
    DB_MUTATE = "db:mutate"
    DB_ADMIN = "db:admin"
    ARCH_DECIDE = "arch:decide"
    # Grafting another extension onto a live, still-executing workstream: a
    # deliberate mid-course scope decision, distinct from the eager
    # whole-tree decomposition the approval-time gate already covers.
    PLAN_EXTEND_WORKSTREAM = "plan:extend_workstream"
    TOOL_CREATE = "tool:create"
    MEMORY_READ = "memory:read"
    KNOWLEDGE_INGEST = "knowledge:ingest"
    KNOWLEDGE_REINDEX = "knowledge:reindex"
    BROWSER_NAVIGATE = "browser:navigate"
    BROWSER_SCREENSHOT = "browser:screenshot"
    BROWSER_DIFF = "browser:diff"
    BROWSER_ACCESSIBILITY_SCAN = "browser:accessibility_scan"
    BROWSER_SPEC = "browser:spec"
    EXTERNAL_DATA_REQUEST = "external_data:request"
    RESEARCH_RUN = "research:run"
    DESKTOP_LAUNCH = "desktop:launch"
    DESKTOP_CLICK = "desktop:click"
    DESKTOP_TYPE = "desktop:type"
    DESKTOP_KEY = "desktop:key"
    DESKTOP_SCREENSHOT = "desktop:screenshot"
    DESKTOP_SCROLL = "desktop:scroll"
