"""Sub-constraint enforcement -- checks tool invocations against granular constraints.

Evaluates tool arguments and metadata against the resolved
``ToolSubConstraints`` for the agent's access level.  Returns a
``SubConstraintViolation`` when a constraint is breached, or
``None`` when all constraints are satisfied.

This layer sits between category-level permission gating and
tool execution in the ``ToolInvoker`` pipeline.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict

from synthorg.core.enums import ActionType, ToolCategory
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.sub_constraint import (
    SUB_CONSTRAINT_DENIED,
    SUB_CONSTRAINT_ENFORCED,
)
from synthorg.tools.sub_constraints import (
    GitAccess,
    NetworkMode,
    TerminalAccess,
    ToolSubConstraints,
)

logger = get_logger(__name__)


class SubConstraintViolation(BaseModel):
    """Details of a sub-constraint violation.

    Attributes:
        constraint: Name of the violated constraint dimension.
        reason: Human-readable explanation of the violation.
        requires_approval: If ``True``, the action can proceed
            with human approval (escalation).  If ``False``, the
            action is unconditionally denied.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    constraint: NotBlankStr
    reason: NotBlankStr
    requires_approval: bool = False


# Action types that involve pushing or external git writes.
_GIT_PUSH_ACTIONS: frozenset[str] = frozenset(
    {
        ActionType.VCS_PUSH,
    }
)

# Tool names that perform external git fetch (clone).
# NOTE: name-based check because GitCloneTool shares VCS_READ with
# read-only git tools (status, log, diff).  A dedicated VCS_CLONE
# action type would allow action-type-based checking.
_GIT_CLONE_TOOL_NAMES: frozenset[str] = frozenset({"git_clone"})

# Action types that require outbound network access.
_NETWORK_ACTION_TYPES: frozenset[str] = frozenset(
    {
        ActionType.COMMS_EXTERNAL,
    }
)


class SubConstraintEnforcer:
    """Enforces granular sub-constraints on tool invocations.

    Checks each constraint dimension in order and returns the first
    violation found, or ``None`` if all checks pass.

    Examples:
        Create from resolved constraints::

            enforcer = SubConstraintEnforcer(constraints)
            violation = enforcer.check(
                "http_request", ToolCategory.WEB, "comms:external", {}
            )
            if violation:
                # deny or escalate
    """

    def __init__(self, constraints: ToolSubConstraints) -> None:
        """Initialize with resolved sub-constraints.

        Args:
            constraints: The effective sub-constraints for the agent.
        """
        self._constraints = constraints

    @property
    def constraints(self) -> ToolSubConstraints:
        """The effective sub-constraints."""
        return self._constraints

    def check(
        self,
        tool_name: str,
        category: ToolCategory,
        action_type: str,
        arguments: dict[str, Any],  # noqa: ARG002  -- reserved for filesystem scope checks
    ) -> SubConstraintViolation | None:
        """Check a tool invocation against all sub-constraint dimensions.

        Evaluates constraints in order: network, terminal, git,
        requires_approval.  Returns the first violation found.

        Args:
            tool_name: Name of the tool being invoked.
            category: Tool category.
            action_type: Security action type string.
            arguments: Tool call arguments (for path validation etc.).

        Returns:
            A ``SubConstraintViolation`` if any constraint is breached,
            or ``None`` if all checks pass.
        """
        for violation in (
            self._check_network(tool_name, action_type),
            self._check_terminal(tool_name, category),
            self._check_git(tool_name, action_type),
            self._check_requires_approval(tool_name, action_type),
        ):
            if violation is not None:
                logger.warning(
                    SUB_CONSTRAINT_DENIED,
                    tool_name=tool_name,
                    constraint=violation.constraint,
                    reason=violation.reason,
                )
                return violation

        logger.debug(
            SUB_CONSTRAINT_ENFORCED,
            tool_name=tool_name,
            category=category.value,
        )
        return None

    def _check_network(
        self,
        tool_name: str,
        action_type: str,
    ) -> SubConstraintViolation | None:
        """Enforce network mode constraint.

        ``NONE`` blocks tools that perform outbound network requests
        (``COMMS_EXTERNAL`` action type) and git clone (external fetch).
        Local-only tools like ``HtmlParserTool`` (``CODE_READ``) are
        allowed even under ``NONE``.

        Returns:
            A ``SubConstraintViolation`` if the constraint is breached,
            or ``None`` if satisfied.
        """
        if self._constraints.network != NetworkMode.NONE:
            return None
        if action_type in _NETWORK_ACTION_TYPES or tool_name in _GIT_CLONE_TOOL_NAMES:
            return SubConstraintViolation(
                constraint="network",
                reason=(
                    f"Tool {tool_name!r} requires network access, "
                    f"but network mode is 'none'"
                ),
            )
        return None

    def _check_terminal(
        self,
        tool_name: str,
        category: ToolCategory,
    ) -> SubConstraintViolation | None:
        """Enforce terminal access constraint.

        ``NONE`` blocks all tools in the TERMINAL category.
        ``RESTRICTED_COMMANDS`` and ``FULL`` are enforced by the
        terminal tool's command allow/blocklist, not here.

        Returns:
            A ``SubConstraintViolation`` if the constraint is breached,
            or ``None`` if satisfied.
        """
        if (
            self._constraints.terminal == TerminalAccess.NONE
            and category == ToolCategory.TERMINAL
        ):
            return SubConstraintViolation(
                constraint="terminal",
                reason=(
                    f"Tool {tool_name!r} requires terminal access, "
                    f"but terminal access is 'none'"
                ),
            )
        return None

    def _check_git(
        self,
        tool_name: str,
        action_type: str,
    ) -> SubConstraintViolation | None:
        """Enforce git access constraint.

        ``LOCAL_ONLY`` blocks clone (external fetch) and push.
        ``READ_AND_BRANCH`` blocks push but allows clone and branching.

        Returns:
            A ``SubConstraintViolation`` if the constraint is breached,
            or ``None`` if satisfied.
        """
        git_access = self._constraints.git

        if git_access == GitAccess.FULL:
            return None

        if git_access == GitAccess.LOCAL_ONLY:
            if action_type in _GIT_PUSH_ACTIONS:
                return SubConstraintViolation(
                    constraint="git",
                    reason=(
                        f"Tool {tool_name!r} performs git push, "
                        f"but git access is 'local_only'"
                    ),
                )
            # Block clone for local_only (by tool name -- see note above)
            if tool_name in _GIT_CLONE_TOOL_NAMES:
                return SubConstraintViolation(
                    constraint="git",
                    reason=(
                        f"Tool {tool_name!r} performs git clone, "
                        f"but git access is 'local_only'"
                    ),
                )

        if git_access == GitAccess.READ_AND_BRANCH and action_type in _GIT_PUSH_ACTIONS:
            return SubConstraintViolation(
                constraint="git",
                reason=(
                    f"Tool {tool_name!r} performs git push, "
                    f"but git access is 'read_and_branch'"
                ),
            )

        return None

    def _check_requires_approval(
        self,
        tool_name: str,
        action_type: str,
    ) -> SubConstraintViolation | None:
        """Check if the action type requires human approval.

        Matches against the ``requires_approval`` prefixes.  A match
        returns a violation with ``requires_approval=True``, signaling
        the invoker to escalate rather than deny.

        Returns:
            A ``SubConstraintViolation`` if the constraint is breached,
            or ``None`` if satisfied.
        """
        for prefix in self._constraints.requires_approval:
            if action_type.startswith(prefix):
                return SubConstraintViolation(
                    constraint="requires_approval",
                    reason=(
                        f"Tool {tool_name!r} with action type "
                        f"{action_type!r} requires human approval"
                    ),
                    requires_approval=True,
                )
        return None
