"""Tool discovery/disclosure mixin for ``ToolInvoker``.

Owns ``get_permitted_definitions``, ``get_l1_summaries``,
``get_loaded_definitions``, ``get_l2_body``, and ``get_l3_resource``.
Relies on ``_registry`` and ``_permission_checker`` declared on the
concrete invoker.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_INVOKE_NOT_FOUND,
    TOOL_PERMISSION_DENIED,
)
from synthorg.tools.errors import ToolNotFoundError

if TYPE_CHECKING:
    from synthorg.core.tool_disclosure import (
        ToolL1Metadata,
        ToolL2Body,
        ToolL3Resource,
    )
    from synthorg.providers.models import ToolDefinition
    from synthorg.tools.permissions import ToolPermissionChecker
    from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)


class ToolInvokerDiscoveryMixin:
    """Discovery/disclosure methods for ``ToolInvoker``."""

    _registry: ToolRegistry
    _permission_checker: ToolPermissionChecker | None

    def get_permitted_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return tool definitions filtered by the permission checker.

        When no permission checker is set, returns all definitions.

        Returns:
            Tuple of permitted tool definitions, sorted by name.
        """
        if self._permission_checker is None:
            return self._registry.to_definitions()
        return self._permission_checker.filter_definitions(self._registry)

    def get_l1_summaries(self) -> tuple[ToolL1Metadata, ...]:
        """Return L1 metadata for all permitted tools.

        For system prompt injection -- lightweight summaries that
        let the agent discover available tools without loading
        full definitions.  Malformed tools are logged and skipped.

        Returns:
            Sorted tuple of L1 metadata for permitted tools.
        """
        from synthorg.observability.events.tool import (  # noqa: PLC0415
            TOOL_DISCLOSURE_L1_SUMMARY_ERROR,
        )

        result: list[ToolL1Metadata] = []
        for name in self._registry.list_tools():
            try:
                tool = self._registry.get(name)
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    TOOL_DISCLOSURE_L1_SUMMARY_ERROR,
                    tool_name=name,
                    note="registry lookup failed during L1 summary",
                )
                continue
            if (
                self._permission_checker is not None
                and not self._permission_checker.is_permitted(name, tool.category)
            ):
                continue
            try:
                result.append(tool.to_l1_metadata())
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    TOOL_DISCLOSURE_L1_SUMMARY_ERROR,
                    tool_name=name,
                    note="to_l1_metadata() failed",
                )
        result.sort(key=lambda m: m.name)
        return tuple(result)

    def get_loaded_definitions(
        self,
        loaded_tools: frozenset[str],
    ) -> tuple[ToolDefinition, ...]:
        """Return full definitions for loaded tools + discovery tools.

        Only tools in ``loaded_tools`` get their full
        ``ToolDefinition`` (with L2 body) included.  The three
        discovery tools (``list_tools``, ``load_tool``,
        ``load_tool_resource``) are always included.

        Args:
            loaded_tools: Tool names with L2 active.

        Returns:
            Sorted tuple of full definitions for loaded and
            discovery tools only.
        """
        from synthorg.tools.discovery import _DISCOVERY_NAMES  # noqa: PLC0415

        target_names = set(loaded_tools) | _DISCOVERY_NAMES
        included: list[ToolDefinition] = []
        for name in sorted(target_names):
            try:
                tool = self._registry.get(name)
            except ToolNotFoundError:
                continue
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    TOOL_INVOKE_NOT_FOUND,
                    tool_name=name,
                    note="unexpected error during loaded definition lookup",
                )
                continue
            if name not in _DISCOVERY_NAMES and (
                self._permission_checker is not None
                and not self._permission_checker.is_permitted(name, tool.category)
            ):
                continue
            try:
                included.append(tool.to_definition())
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    TOOL_INVOKE_NOT_FOUND,
                    tool_name=name,
                    note="to_definition() failed during loaded definition lookup",
                )
        return tuple(included)

    def get_l2_body(self, tool_name: str) -> ToolL2Body | None:
        """Return L2 body for a specific permitted tool.

        Args:
            tool_name: Name of the tool.

        Returns:
            The L2 body, or ``None`` if the tool is not found
            or not permitted.
        """
        try:
            tool = self._registry.get(tool_name)
        except ToolNotFoundError:
            logger.debug(
                TOOL_INVOKE_NOT_FOUND,
                tool_name=tool_name,
                note="tool not found during L2 disclosure query",
            )
            return None
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TOOL_INVOKE_NOT_FOUND,
                tool_name=tool_name,
                note="unexpected error during disclosure lookup",
            )
            return None
        if (
            self._permission_checker is not None
            and not self._permission_checker.is_permitted(tool_name, tool.category)
        ):
            logger.debug(
                TOOL_PERMISSION_DENIED,
                tool_name=tool_name,
                note="permission denied during L2 disclosure query",
            )
            return None
        try:
            return tool.to_l2_body()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TOOL_INVOKE_NOT_FOUND,
                tool_name=tool_name,
                note="to_l2_body() failed during disclosure query",
            )
            return None

    def get_l3_resource(
        self,
        tool_name: str,
        resource_id: str,
    ) -> ToolL3Resource | None:
        """Return a specific L3 resource for a permitted tool.

        Args:
            tool_name: Name of the tool.
            resource_id: Identifier of the resource.

        Returns:
            The L3 resource, or ``None`` if not found or
            not permitted.
        """
        try:
            tool = self._registry.get(tool_name)
        except ToolNotFoundError:
            logger.debug(
                TOOL_INVOKE_NOT_FOUND,
                tool_name=tool_name,
                resource_id=resource_id,
                note="tool not found during L3 disclosure query",
            )
            return None
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TOOL_INVOKE_NOT_FOUND,
                tool_name=tool_name,
                resource_id=resource_id,
                note="unexpected error during disclosure lookup",
            )
            return None
        if (
            self._permission_checker is not None
            and not self._permission_checker.is_permitted(tool_name, tool.category)
        ):
            logger.debug(
                TOOL_PERMISSION_DENIED,
                tool_name=tool_name,
                resource_id=resource_id,
                note="permission denied during L3 disclosure query",
            )
            return None
        try:
            resources = tool.get_l3_resources()
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TOOL_INVOKE_NOT_FOUND,
                tool_name=tool_name,
                resource_id=resource_id,
                note="get_l3_resources() failed during disclosure query",
            )
            return None
        return next(
            (r for r in resources if r.resource_id == resource_id),
            None,
        )
