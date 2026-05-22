"""``QueryStructureMapTool`` -- structured navigation of the codebase map.

Unlike ``search_knowledge`` (free-text retrieval), this tool answers
structured questions over the deterministic brownfield structure map:
list modules, entry points, tests, build files, or dependencies. The
imported codebase is third-party content, so every rendered field is
wrapped via ``wrap_untrusted`` before it can reach an agent prompt.
"""

import builtins
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel  # noqa: TC002 -- ClassVar runtime ref

from synthorg.api.boundary import parse_typed
from synthorg.core.enums import ActionType, ToolCategory
from synthorg.core.persistence_errors import QueryError
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.brownfield import (
    BROWNFIELD_STRUCTURE_QUERY_FAILED,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.structure_map._args import (
    QueryStructureMapArgs,
    StructureMapFacet,
)

if TYPE_CHECKING:
    from synthorg.core.codebase_structure_map import CodebaseStructureMap
    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.codebase_structure_map_protocol import (
        CodebaseStructureMapRepository,
    )

logger = get_logger(__name__)


class QueryStructureMapTool(BaseTool):
    """Agent tool that lists facets of a project's codebase structure map."""

    args_model: ClassVar[type[BaseModel] | None] = QueryStructureMapArgs

    def __init__(
        self,
        *,
        repository: CodebaseStructureMapRepository,
        project_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name="query_structure_map",
            description=(
                "List the modules, entry points, test suites, build files, or "
                "dependencies of the imported codebase's structure map. Use this "
                "to navigate the codebase before making changes."
            ),
            parameters_schema=QueryStructureMapArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
            action_type=ActionType.MEMORY_READ.value,
        )
        self._repository = repository
        self._project_id = project_id

    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Dispatch a ``query_structure_map`` invocation to the repository."""
        try:
            parsed = parse_typed("mcp.tool", arguments, QueryStructureMapArgs)
            structure_map = await self._repository.get(self._project_id)
        except (ValueError, TypeError) as exc:
            return ToolExecutionResult(
                content=(
                    f"Query failed: invalid arguments ({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        except builtins.MemoryError, RecursionError:
            raise
        except QueryError as exc:
            logger.error(
                BROWNFIELD_STRUCTURE_QUERY_FAILED,
                project_id=self._project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Query failed: {safe_error_description(exc)}",
                is_error=True,
            )
        if structure_map is None:
            return ToolExecutionResult(
                content="No codebase structure map exists for this project yet.",
                is_error=False,
            )
        rendered = _render_facet(structure_map, parsed.facet, parsed.name_filter)
        return ToolExecutionResult(
            content=wrap_untrusted(TAG_TASK_DATA, rendered),
            metadata={"facet": parsed.facet.value},
        )


def _render_facet(
    structure_map: CodebaseStructureMap,
    facet: StructureMapFacet,
    name_filter: str | None,
) -> str:
    """Render the requested facet as one entry per line."""
    lines = _facet_lines(structure_map, facet)
    if name_filter is not None:
        needle = name_filter.casefold()
        lines = [line for line in lines if needle in line.casefold()]
    if not lines:
        return f"No {facet.value} found in the structure map."
    return "\n".join(lines)


def _facet_lines(
    structure_map: CodebaseStructureMap,
    facet: StructureMapFacet,
) -> list[str]:
    """Produce the per-entry rendering for *facet*."""
    if facet is StructureMapFacet.MODULES:
        return [
            f"{m.path} [{m.language.value}, {m.kind.value}]"
            for m in structure_map.modules
        ]
    if facet is StructureMapFacet.ENTRY_POINTS:
        return [
            f"{e.path} [{e.kind.value}]" + (f" -> {e.command}" if e.command else "")
            for e in structure_map.entry_points
        ]
    if facet is StructureMapFacet.TEST_SUITES:
        return [
            f"{t.path}" + (f" [{t.framework}]" if t.framework else "")
            for t in structure_map.test_suites
        ]
    if facet is StructureMapFacet.BUILD_FILES:
        return [f"{b.path} [{b.tool}]" for b in structure_map.build_files]
    return [
        f"{d.name}"
        + (f" {d.version_spec}" if d.version_spec else "")
        + f" [{d.ecosystem.value}, {d.scope.value}]"
        for d in structure_map.dependencies
    ]


__all__ = ["QueryStructureMapTool"]
