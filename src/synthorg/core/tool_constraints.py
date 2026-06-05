"""Sub-constraint models for granular tool access control.

Defines per-dimension constraint enums and a ``ToolSubConstraints``
model that captures file system scope, network mode, git access,
code execution isolation, and terminal access.  Each
``ToolAccessLevel`` maps to a default set of sub-constraints
matching the design spec (operations.md, section 11.2).

``get_sub_constraints`` resolves the effective constraints for an
access level, optionally overriding with per-agent custom constraints.

Lives in ``core`` (the foundation layer) so leaf domain models such as
``core.agent.AgentIdentity`` can reference ``ToolSubConstraints`` without
importing the ``tools`` package, whose ``__init__`` eagerly pulls the
provider/engine stack and would re-introduce a cold-import cycle.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.sub_constraint import SUB_CONSTRAINT_RESOLVED

logger = get_logger(__name__)


# ── Constraint dimension enums ─────────────────────────────────


class FileSystemScope(StrEnum):
    """File system access scope.

    Attributes:
        WORKSPACE_ONLY: Restrict to the agent's workspace directory.
        PROJECT_DIRECTORY: Access within the project directory tree.
        FULL: Unrestricted file system access.
    """

    WORKSPACE_ONLY = "workspace_only"
    PROJECT_DIRECTORY = "project_directory"
    FULL = "full"


class NetworkMode(StrEnum):
    """Network access mode.

    Attributes:
        NONE: No network access permitted.
        ALLOWLIST_ONLY: Only configured host:port pairs allowed.
        OPEN: Unrestricted outbound network access.
    """

    NONE = "none"
    ALLOWLIST_ONLY = "allowlist_only"
    OPEN = "open"


class GitAccess(StrEnum):
    """Git access level.

    Attributes:
        LOCAL_ONLY: Git operations only within the workspace.
        READ_AND_BRANCH: Read operations plus branch creation (no push).
        FULL: All git operations including push.
    """

    LOCAL_ONLY = "local_only"
    READ_AND_BRANCH = "read_and_branch"
    FULL = "full"


class CodeExecutionIsolation(StrEnum):
    """Code execution isolation level.

    Attributes:
        CONTAINERIZED: Run in a container (Docker/K8s sandbox).
        PROCESS: Run as a sandboxed subprocess (lighter isolation).
    """

    CONTAINERIZED = "containerized"
    PROCESS = "process"


class TerminalAccess(StrEnum):
    """Terminal command access level.

    Attributes:
        NONE: No terminal access.
        RESTRICTED_COMMANDS: Only allow/blocklist-filtered commands.
        FULL: Unrestricted command execution.
    """

    NONE = "none"
    RESTRICTED_COMMANDS = "restricted_commands"
    FULL = "full"


# ── Sub-constraints model ──────────────────────────────────────


class ToolSubConstraints(BaseModel):
    """Per-level sub-constraints for tool access control.

    Each dimension restricts a specific aspect of tool behavior.
    The ``requires_approval`` tuple lists action type prefixes that
    require human approval before execution.

    Attributes:
        file_system: File system access scope.
        network: Network access mode.
        git: Git access level.
        code_execution: Code execution isolation level.
        terminal: Terminal command access level.
        requires_approval: Action type prefixes requiring approval.
        network_allowlist: Allowed host:port pairs when network
            mode is ``ALLOWLIST_ONLY``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    file_system: FileSystemScope = Field(
        default=FileSystemScope.PROJECT_DIRECTORY,
        description="File system access scope",
    )
    network: NetworkMode = Field(
        default=NetworkMode.OPEN,
        description="Network access mode",
    )
    git: GitAccess = Field(
        default=GitAccess.FULL,
        description="Git access level",
    )
    code_execution: CodeExecutionIsolation = Field(
        default=CodeExecutionIsolation.CONTAINERIZED,
        description="Code execution isolation level",
    )
    terminal: TerminalAccess = Field(
        default=TerminalAccess.RESTRICTED_COMMANDS,
        description="Terminal command access level",
    )
    requires_approval: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Action type prefixes requiring human approval",
    )
    network_allowlist: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Allowed host:port pairs for allowlist_only network mode",
    )


# ── Level defaults ─────────────────────────────────────────────

_LEVEL_SUB_CONSTRAINTS: Final[MappingProxyType[ToolAccessLevel, ToolSubConstraints]] = (
    MappingProxyType(
        {
            ToolAccessLevel.SANDBOXED: ToolSubConstraints(
                file_system=FileSystemScope.WORKSPACE_ONLY,
                network=NetworkMode.NONE,
                git=GitAccess.LOCAL_ONLY,
                code_execution=CodeExecutionIsolation.CONTAINERIZED,
                terminal=TerminalAccess.NONE,
            ),
            ToolAccessLevel.RESTRICTED: ToolSubConstraints(
                file_system=FileSystemScope.PROJECT_DIRECTORY,
                network=NetworkMode.ALLOWLIST_ONLY,
                git=GitAccess.READ_AND_BRANCH,
                code_execution=CodeExecutionIsolation.CONTAINERIZED,
                terminal=TerminalAccess.NONE,
                requires_approval=("deploy:", "db:mutate"),
            ),
            ToolAccessLevel.STANDARD: ToolSubConstraints(
                file_system=FileSystemScope.PROJECT_DIRECTORY,
                network=NetworkMode.OPEN,
                git=GitAccess.FULL,
                code_execution=CodeExecutionIsolation.CONTAINERIZED,
                terminal=TerminalAccess.RESTRICTED_COMMANDS,
            ),
            ToolAccessLevel.ELEVATED: ToolSubConstraints(
                file_system=FileSystemScope.FULL,
                network=NetworkMode.OPEN,
                git=GitAccess.FULL,
                code_execution=CodeExecutionIsolation.CONTAINERIZED,
                terminal=TerminalAccess.FULL,
            ),
        }
    )
)


# ── Resolution ─────────────────────────────────────────────────


def get_sub_constraints(
    access_level: ToolAccessLevel,
    custom_constraints: ToolSubConstraints | None = None,
) -> ToolSubConstraints:
    """Resolve effective sub-constraints for an access level.

    For ``CUSTOM`` access level, ``custom_constraints`` is required.
    For built-in levels, returns the spec-defined defaults unless
    ``custom_constraints`` overrides them.

    Args:
        access_level: The agent's tool access level.
        custom_constraints: Optional per-agent overrides. For
            ``CUSTOM`` level, used as-is (full specification required).
            For built-in levels, explicitly set fields override the
            level defaults while unset fields inherit the secure
            baseline (overlay merge via ``model_dump(exclude_unset)``).

    Returns:
        The resolved ``ToolSubConstraints``.

    Raises:
        ValueError: If ``CUSTOM`` level is used without providing
            ``custom_constraints``.
    """
    if custom_constraints is not None:
        if access_level == ToolAccessLevel.CUSTOM:
            logger.debug(
                SUB_CONSTRAINT_RESOLVED,
                access_level=access_level.value,
                source="custom",
            )
            return custom_constraints
        # Non-CUSTOM with overrides: merge into level defaults so
        # unset fields retain the secure baseline.
        base = _LEVEL_SUB_CONSTRAINTS.get(access_level, ToolSubConstraints())
        merged = {
            **base.model_dump(),
            **custom_constraints.model_dump(exclude_unset=True),
        }
        logger.debug(
            SUB_CONSTRAINT_RESOLVED,
            access_level=access_level.value,
            source="merged",
        )
        return ToolSubConstraints(**merged)

    if access_level == ToolAccessLevel.CUSTOM:
        msg = (
            "CUSTOM access level requires explicit sub_constraints; none were provided"
        )
        logger.warning(
            SUB_CONSTRAINT_RESOLVED,
            access_level=access_level.value,
            source="error",
            error=msg,
        )
        raise ValueError(msg)

    constraints = _LEVEL_SUB_CONSTRAINTS.get(access_level)
    if constraints is None:
        msg = f"No default sub-constraints for access level: {access_level.value}"
        logger.warning(
            SUB_CONSTRAINT_RESOLVED,
            access_level=access_level.value,
            source="error",
            error=msg,
        )
        raise ValueError(msg)
    logger.debug(
        SUB_CONSTRAINT_RESOLVED,
        access_level=access_level.value,
        source="level_default",
    )
    return constraints
