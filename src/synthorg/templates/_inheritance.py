"""Template inheritance resolution.

Handles parent chain loading, circular detection, depth limits,
parent-child merge, variable flow, and post-merge name deduplication.
Extracted from ``renderer.py`` to keep file sizes under 800 lines.
"""

from typing import TYPE_CHECKING, Any, Final, Protocol

from synthorg.observability import get_logger
from synthorg.observability.events.template import (
    TEMPLATE_INHERIT_CIRCULAR,
    TEMPLATE_INHERIT_DEPTH_EXCEEDED,
)
from synthorg.templates.errors import TemplateInheritanceError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.templates.loader import LoadedTemplate
    from synthorg.templates.schema import CompanyTemplate


# Self-referential callback for render_parent_config; consumer:
# templates.renderer._render_to_dict passes itself in to walk the parent chain.
class _RenderToDictFn(Protocol):
    """Callback protocol for ``_render_to_dict``."""

    def __call__(
        self,
        loaded: LoadedTemplate,
        variables: dict[str, Any] | None = ...,
        *,
        locales: list[str] | None = ...,
        _chain: frozenset[str] = ...,
        custom_presets: Mapping[str, dict[str, Any]] | None = ...,
        _as_parent: bool = ...,
    ) -> dict[str, Any]: ...


logger = get_logger(__name__)

# Maximum inheritance chain depth.
_MAX_INHERITANCE_DEPTH: Final[int] = 10


def _validate_inheritance_chain(
    child_id: str,
    parent_name: str,
    _chain: frozenset[str],
) -> None:
    """Check for circular inheritance and depth overflow.

    Raises:
        TemplateInheritanceError: When ``parent_name`` is already in the
            chain (circular) or the chain exceeds the depth limit.
    """
    if parent_name in _chain:
        logger.error(
            TEMPLATE_INHERIT_CIRCULAR,
            child=child_id,
            parent=parent_name,
            chain=sorted(_chain),
        )
        msg = (
            f"Circular template inheritance: {child_id!r} extends "
            f"{parent_name!r}, which is already in the inheritance chain"
        )
        raise TemplateInheritanceError(msg)

    if len(_chain) >= _MAX_INHERITANCE_DEPTH:
        logger.error(
            TEMPLATE_INHERIT_DEPTH_EXCEEDED,
            child=child_id,
            depth=len(_chain),
            max_depth=_MAX_INHERITANCE_DEPTH,
        )
        msg = (
            f"Template inheritance depth exceeded ({len(_chain)} >= "
            f"{_MAX_INHERITANCE_DEPTH}): {child_id!r}"
        )
        raise TemplateInheritanceError(msg)


def render_parent_config(  # noqa: PLR0913
    *,
    parent_name: str,
    child_id: str,
    vars_dict: dict[str, Any],
    _chain: frozenset[str],
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, Any]] | None = None,
    render_to_dict_fn: _RenderToDictFn,
) -> dict[str, Any]:
    """Load and render a parent template, returning its config dict.

    Does **not** merge with a child config.  Used by the renderer to
    obtain the parent config dict without merging, enabling the caller
    to layer packs between parent and child before the final merge.

    Args:
        parent_name: Parent template name.
        child_id: Source name of the child template (for error
            messages and circular detection).
        vars_dict: Child's resolved variables.
        _chain: Already-visited parent names for circular detection.
        locales: Faker locale codes for auto-name generation.
        custom_presets: Optional custom preset mapping.
        render_to_dict_fn: Callback to ``_render_to_dict``.

    Returns:
        Rendered parent config dict.

    Raises:
        TemplateInheritanceError: On circular chains or depth overflow.
    """
    from synthorg.templates.loader import load_template  # noqa: PLC0415

    _validate_inheritance_chain(child_id, parent_name, _chain)

    parent_loaded = load_template(parent_name)
    parent_vars = collect_parent_variables(
        parent_loaded.template,
        vars_dict,
    )
    return render_to_dict_fn(
        parent_loaded,
        parent_vars,
        locales=locales,
        _chain=_chain | {parent_name},
        custom_presets=custom_presets,
        _as_parent=True,
    )


def deduplicate_merged_agent_names(merged: dict[str, Any]) -> dict[str, Any]:
    """Ensure agent names are unique after merging parent + child.

    Parent and child agent names are auto-generated independently, so
    collisions can occur.  This mirrors the per-pass deduplication in
    ``_expand_single_agent`` but operates on the post-merge agent list.

    Returns a new dict with a rebuilt agents list.

    Args:
        merged: Post-merge config dict.

    Returns:
        New config dict with deduplicated agent names.
    """
    agents = merged.get("agents")
    if not agents:
        return merged
    used: set[str] = set()
    new_agents: list[dict[str, Any]] = []
    for agent in agents:
        name = agent.get("name") or ""
        if not name:
            new_agents.append(agent)
            continue
        if name in used:
            base = name
            counter = 2
            while name in used:
                name = f"{base} {counter}"
                counter += 1
            new_agents.append({**agent, "name": name})
        else:
            new_agents.append(agent)
        used.add(name)
    return {**merged, "agents": new_agents}


def collect_parent_variables(
    parent_template: CompanyTemplate,
    child_vars: dict[str, Any],
) -> dict[str, Any]:
    """Collect variables for a parent template.

    Child's resolved variables take priority over the parent's own
    defaults.  Parent-declared defaults fill any remaining gaps.

    Args:
        parent_template: The parent template.
        child_vars: Child's resolved variables.

    Returns:
        Variable dict for parent rendering.
    """
    result: dict[str, Any] = dict(child_vars)
    for var in parent_template.variables:
        if var.name not in result and var.default is not None:
            result[var.name] = var.default
    return result
