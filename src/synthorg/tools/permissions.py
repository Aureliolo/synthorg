"""Tool permission checker -- enforces access-level gating and sub-constraints.

Resolves tool permissions using a priority-based system:
1. If tool name is in ``denied`` → **DENIED**
2. If tool name is in ``allowed`` → **ALLOWED**
3. If access level is ``CUSTOM`` → **DENIED**
4. If tool category is in the level's allowed categories → **ALLOWED**
5. Otherwise → **DENIED**

After category-level gating, an optional ``SubConstraintEnforcer``
checks granular sub-constraints (network mode, terminal access, git
access, requires_approval) against the tool invocation.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Self

from synthorg.core.enums import ToolAccessLevel, ToolCategory
from synthorg.core.normalization import normalize_identifier
from synthorg.core.tool_constraints import ToolSubConstraints, get_sub_constraints
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_PERMISSION_CHECKER_CREATED,
    TOOL_PERMISSION_DENIED,
    TOOL_PERMISSION_FILTERED,
)
from synthorg.tools.sub_constraint_enforcer import (
    SubConstraintEnforcer,
    SubConstraintViolation,
)

from .errors import ToolPermissionDeniedError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import ToolPermissions
    from synthorg.core.tool_disclosure import ToolL1Metadata
    from synthorg.providers.models import ToolDefinition

    from .registry import ToolRegistry

logger = get_logger(__name__)


class ToolPermissionChecker:
    """Enforces tool access permissions based on access level and explicit lists.

    Each access level grants a set of tool categories. Explicit ``allowed``
    and ``denied`` lists override the level-based rules (denied has highest
    priority). All name matching is case-insensitive.

    Examples:
        Create from agent permissions::

            checker = ToolPermissionChecker.from_permissions(identity.tools)
            if checker.is_permitted("git_push", ToolCategory.VERSION_CONTROL):
                ...

        Filter tool definitions for LLM prompt::

            defs = checker.filter_definitions(registry)
    """

    _LEVEL_CATEGORIES: ClassVar[Mapping[ToolAccessLevel, frozenset[ToolCategory]]] = (
        MappingProxyType(
            {
                # MEMORY is read-only (no write tool exists); agent
                # isolation is enforced by the MemoryBackend, not here.
                ToolAccessLevel.SANDBOXED: frozenset(
                    {
                        ToolCategory.FILE_SYSTEM,
                        ToolCategory.CODE_EXECUTION,
                        ToolCategory.VERSION_CONTROL,
                        ToolCategory.MEMORY,
                    }
                ),
                ToolAccessLevel.RESTRICTED: frozenset(
                    {
                        ToolCategory.FILE_SYSTEM,
                        ToolCategory.CODE_EXECUTION,
                        ToolCategory.VERSION_CONTROL,
                        ToolCategory.WEB,
                        ToolCategory.MEMORY,
                    }
                ),
                ToolAccessLevel.STANDARD: frozenset(
                    {
                        ToolCategory.FILE_SYSTEM,
                        ToolCategory.CODE_EXECUTION,
                        ToolCategory.VERSION_CONTROL,
                        ToolCategory.WEB,
                        ToolCategory.TERMINAL,
                        ToolCategory.ANALYTICS,
                        ToolCategory.MEMORY,
                        ToolCategory.BROWSER,
                        ToolCategory.EXTERNAL_DATA,
                        ToolCategory.DESKTOP,
                    }
                ),
                # all categories -- new ToolCategory members are auto-included;
                # review new categories with ELEVATED access in mind
                ToolAccessLevel.ELEVATED: frozenset(ToolCategory),
                ToolAccessLevel.CUSTOM: frozenset(),
            }
        )
    )

    def __init__(
        self,
        *,
        access_level: ToolAccessLevel = ToolAccessLevel.STANDARD,
        allowed: frozenset[str] = frozenset(),
        denied: frozenset[str] = frozenset(),
        sub_constraints: ToolSubConstraints | None = None,
    ) -> None:
        """Initialize with access level, explicit name lists, and sub-constraints.

        Args:
            access_level: Base access level for category gating.
            allowed: Explicitly allowed tool names (normalized on store).
            denied: Explicitly denied tool names (normalized on store).
            sub_constraints: Optional per-agent sub-constraints.  When
                ``None``, defaults are resolved from the access level.
                For ``CUSTOM`` level without sub-constraints, no
                sub-constraint enforcement is performed.
        """
        self._access_level = access_level
        self._allowed = frozenset(normalize_identifier(n) for n in allowed)
        self._denied = frozenset(normalize_identifier(n) for n in denied)

        # Resolve sub-constraint enforcer.  For CUSTOM without explicit
        # constraints, sub-constraint enforcement is skipped (only
        # allow/deny lists apply).
        self._sub_enforcer: SubConstraintEnforcer | None = None
        if not (access_level == ToolAccessLevel.CUSTOM and sub_constraints is None):
            resolved = get_sub_constraints(access_level, sub_constraints)
            self._sub_enforcer = SubConstraintEnforcer(resolved)

        logger.debug(
            TOOL_PERMISSION_CHECKER_CREATED,
            access_level=access_level.value,
            allowed_count=len(self._allowed),
            denied_count=len(self._denied),
            has_sub_constraints=self._sub_enforcer is not None,
        )

    @classmethod
    def from_permissions(cls, permissions: ToolPermissions) -> Self:
        """Create a checker from an agent's ``ToolPermissions`` model.

        Args:
            permissions: Agent tool permissions.

        Returns:
            Configured permission checker.
        """
        return cls(
            access_level=permissions.access_level,
            allowed=frozenset(permissions.allowed),
            denied=frozenset(permissions.denied),
            sub_constraints=permissions.sub_constraints,
        )

    def is_permitted(self, tool_name: str, category: ToolCategory) -> bool:
        """Check whether a tool is permitted.

        Args:
            tool_name: Name of the tool.
            category: Category of the tool.

        Returns:
            ``True`` if the tool is permitted, ``False`` otherwise.
        """
        name_lower = normalize_identifier(tool_name)
        if name_lower in self._denied:
            return False
        if name_lower in self._allowed:
            return True
        if self._access_level == ToolAccessLevel.CUSTOM:
            return False
        allowed_cats = self._LEVEL_CATEGORIES[self._access_level]
        return category in allowed_cats

    def check(self, tool_name: str, category: ToolCategory) -> None:
        """Assert that a tool is permitted, raising on denial.

        Args:
            tool_name: Name of the tool.
            category: Category of the tool.

        Raises:
            ToolPermissionDeniedError: If the tool is not permitted.
        """
        if not self.is_permitted(tool_name, category):
            reason = self.denial_reason(tool_name, category)
            logger.warning(
                TOOL_PERMISSION_DENIED,
                tool_name=tool_name,
                category=category.value,
                reason=reason,
            )
            raise ToolPermissionDeniedError(
                reason,
                context={"tool": tool_name, "category": category.value},
            )

    def denial_reason(self, tool_name: str, category: ToolCategory) -> str:
        """Return a human-readable reason why a tool would be denied.

        Intended for use after confirming the tool is denied via
        ``is_permitted`` or via ``check``.  If the tool is actually
        permitted, the returned string does not apply and should not
        be shown to users.

        Args:
            tool_name: Name of the tool.
            category: Category of the tool.

        Returns:
            Explanation string suitable for error messages.
        """
        name_lower = normalize_identifier(tool_name)
        if name_lower in self._denied:
            return f"Tool {tool_name!r} is explicitly denied"
        if self._access_level == ToolAccessLevel.CUSTOM:
            return (
                f"Tool {tool_name!r} is not in the allowed list (access level: custom)"
            )
        return (
            f"Category {category.value!r} is not permitted "
            f"at access level {self._access_level.value!r}"
        )

    def check_sub_constraints(
        self,
        tool_name: str,
        category: ToolCategory,
        action_type: str,
        arguments: dict[str, object],
    ) -> SubConstraintViolation | None:
        """Check granular sub-constraints for a tool invocation.

        Returns ``None`` if all sub-constraints pass, or a
        ``SubConstraintViolation`` if any constraint is breached.
        Always returns ``None`` when no sub-constraint enforcer
        is configured (e.g. bare CUSTOM level).

        Args:
            tool_name: Name of the tool.
            category: Tool category.
            action_type: Security action type string.
            arguments: Tool call arguments.

        Returns:
            A violation or ``None``.
        """
        if self._sub_enforcer is None:
            return None
        return self._sub_enforcer.check(tool_name, category, action_type, arguments)

    def filter_definitions(self, registry: ToolRegistry) -> tuple[ToolDefinition, ...]:
        """Return only permitted tool definitions from a registry.

        Args:
            registry: Tool registry to filter.

        Returns:
            Tuple of permitted tool definitions, sorted by tool name.
        """
        tool_names = registry.list_tools()
        result: list[ToolDefinition] = []
        for name in tool_names:
            tool = registry.get(name)
            if self.is_permitted(name, tool.category):
                result.append(tool.to_definition())
        result.sort(key=lambda d: d.name)
        excluded = len(tool_names) - len(result)
        if excluded:
            logger.debug(
                TOOL_PERMISSION_FILTERED,
                access_level=self._access_level.value,
                total=len(tool_names),
                permitted=len(result),
                excluded=excluded,
            )
        return tuple(result)

    def filter_l1_summaries(
        self,
        registry: ToolRegistry,
    ) -> tuple[ToolL1Metadata, ...]:
        """Return L1 metadata for permitted tools only.

        Lightweight variant of ``filter_definitions`` that returns
        only L1 summaries for system prompt injection.

        Args:
            registry: Tool registry to filter.

        Returns:
            Sorted tuple of L1 metadata for permitted tools.
        """
        tool_names = registry.list_tools()
        result: list[ToolL1Metadata] = []
        for name in tool_names:
            tool = registry.get(name)
            if self.is_permitted(name, tool.category):
                result.append(tool.to_l1_metadata())
        result.sort(key=lambda m: m.name)
        return tuple(result)
