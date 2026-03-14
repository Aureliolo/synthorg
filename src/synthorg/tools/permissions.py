"""Tool permission checker — enforces access-level gating.

Resolves tool permissions using a priority-based system:
1. If tool name is in ``denied`` → **DENIED**
2. If tool name is in ``allowed`` → **ALLOWED**
3. If access level is ``CUSTOM`` → **DENIED**
4. If tool category is in the level's allowed categories → **ALLOWED**
5. Otherwise → **DENIED**
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Self

from synthorg.core.enums import ToolAccessLevel, ToolCategory
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_PERMISSION_CHECKER_CREATED,
    TOOL_PERMISSION_DENIED,
    TOOL_PERMISSION_FILTERED,
)

from .errors import ToolPermissionDeniedError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import ToolPermissions
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
                ToolAccessLevel.SANDBOXED: frozenset(
                    {
                        ToolCategory.FILE_SYSTEM,
                        ToolCategory.CODE_EXECUTION,
                        ToolCategory.VERSION_CONTROL,
                    }
                ),
                ToolAccessLevel.RESTRICTED: frozenset(
                    {
                        ToolCategory.FILE_SYSTEM,
                        ToolCategory.CODE_EXECUTION,
                        ToolCategory.VERSION_CONTROL,
                        ToolCategory.WEB,
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
                    }
                ),
                # all categories — new ToolCategory members are auto-included;
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
    ) -> None:
        """Initialize with access level and explicit name lists.

        Args:
            access_level: Base access level for category gating.
            allowed: Explicitly allowed tool names (normalized on store).
            denied: Explicitly denied tool names (normalized on store).
        """
        self._access_level = access_level
        self._allowed = frozenset(n.strip().casefold() for n in allowed)
        self._denied = frozenset(n.strip().casefold() for n in denied)
        logger.debug(
            TOOL_PERMISSION_CHECKER_CREATED,
            access_level=access_level.value,
            allowed_count=len(self._allowed),
            denied_count=len(self._denied),
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
        )

    def is_permitted(self, tool_name: str, category: ToolCategory) -> bool:
        """Check whether a tool is permitted.

        Args:
            tool_name: Name of the tool.
            category: Category of the tool.

        Returns:
            ``True`` if the tool is permitted, ``False`` otherwise.
        """
        name_lower = tool_name.strip().casefold()
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
        name_lower = tool_name.strip().casefold()
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
