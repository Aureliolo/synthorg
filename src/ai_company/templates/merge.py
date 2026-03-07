"""Template config merging for inheritance.

Provides ``merge_template_configs`` which combines a parent config dict
with a child config dict, implementing the merge semantics described in
the template inheritance design.
"""

from typing import Any

from ai_company.config.utils import deep_merge
from ai_company.observability import get_logger
from ai_company.observability.events.template import TEMPLATE_INHERIT_MERGE
from ai_company.templates.errors import TemplateInheritanceError

logger = get_logger(__name__)


def merge_template_configs(
    parent: dict[str, Any],
    child: dict[str, Any],
) -> dict[str, Any]:
    """Merge a parent config dict with a child config dict.

    Merge strategies by field:

    - ``company_name``, ``company_type``: child wins if present.
    - ``config`` (dict): deep-merged; child keys override parent.
    - ``agents`` (list): merged by ``(role, department)`` key.
    - ``departments`` (list): merged by ``name`` (case-insensitive).
    - ``workflow_handoffs``, ``escalation_paths``: child replaces
      entirely if present.

    Args:
        parent: Fully-resolved parent config dict.
        child: Fully-resolved child config dict.

    Returns:
        New merged config dict.
    """
    logger.debug(TEMPLATE_INHERIT_MERGE, action="start")

    result: dict[str, Any] = {}

    # Scalars: child wins if present.
    for key in ("company_name", "company_type"):
        if key in child and child[key] is not None:
            result[key] = child[key]
        elif key in parent:
            result[key] = parent[key]

    # Config dict: deep merge.
    parent_config = parent.get("config", {})
    child_config = child.get("config", {})
    if parent_config or child_config:
        result["config"] = deep_merge(
            parent_config if isinstance(parent_config, dict) else {},
            child_config if isinstance(child_config, dict) else {},
        )

    # Agents: merge by (role, department) key.
    parent_agents = parent.get("agents", [])
    child_agents = child.get("agents", [])
    if parent_agents or child_agents:
        result["agents"] = _merge_agents(parent_agents, child_agents)

    # Departments: merge by name.
    parent_depts = parent.get("departments", [])
    child_depts = child.get("departments", [])
    if parent_depts or child_depts:
        result["departments"] = _merge_departments(parent_depts, child_depts)

    # Replace-if-present fields.
    for key in ("workflow_handoffs", "escalation_paths"):
        if key in child and child[key] is not None:
            result[key] = child[key]
        elif key in parent:
            result[key] = parent[key]

    logger.debug(TEMPLATE_INHERIT_MERGE, action="done")
    return result


def _merge_agents(
    parent_agents: list[dict[str, Any]],
    child_agents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge agent lists by ``(role, department)`` key.

    Algorithm:
    1. Index parent agents by ``(role.lower(), department.lower())``.
       Duplicate keys maintain an ordered list per key.
    2. Walk child agents:
       - ``_remove: true``: find first unmatched parent with same key,
         remove it. Child entry is discarded.
       - Otherwise: match against first unmatched parent with same key,
         replace. No match → append.
    3. Strip ``_remove`` markers from output.
    4. Result: parent agents (with replacements/removals) + appended.

    Args:
        parent_agents: Parent agent dicts.
        child_agents: Child agent dicts.

    Returns:
        Merged agent list.

    Raises:
        TemplateInheritanceError: If ``_remove`` has no matching parent.
    """
    # Build indexed list: key -> list of [index, agent_dict, matched].
    parent_entries: dict[tuple[str, str], list[list[Any]]] = {}
    for idx, agent in enumerate(parent_agents):
        key = _agent_key(agent)
        parent_entries.setdefault(key, []).append([idx, agent, False])

    appended: list[dict[str, Any]] = []
    for child_agent in child_agents:
        _apply_child_agent(child_agent, parent_entries, appended)

    return _collect_merged_agents(parent_agents, parent_entries, appended)


def _apply_child_agent(
    child_agent: dict[str, Any],
    parent_entries: dict[tuple[str, str], list[list[Any]]],
    appended: list[dict[str, Any]],
) -> None:
    """Apply a single child agent against parent entries.

    Mutates *parent_entries* and *appended* in place.
    """
    key = _agent_key(child_agent)
    is_remove = child_agent.get("_remove", False)
    entries = parent_entries.get(key, [])

    matched_entry = _find_unmatched(entries)
    clean = {k: v for k, v in child_agent.items() if k != "_remove"}

    if is_remove:
        if matched_entry is None:
            msg = f"Cannot remove agent with key {key}: no matching parent agent found"
            raise TemplateInheritanceError(msg)
        matched_entry[2] = True  # mark matched
        matched_entry[1] = None  # mark for removal
    elif matched_entry is not None:
        matched_entry[2] = True
        matched_entry[1] = clean
    else:
        appended.append(clean)


def _find_unmatched(
    entries: list[list[Any]],
) -> list[Any] | None:
    """Find first unmatched entry in a parent entries list."""
    for entry in entries:
        if not entry[2]:
            return entry
    return None


def _collect_merged_agents(
    parent_agents: list[dict[str, Any]],
    parent_entries: dict[tuple[str, str], list[list[Any]]],
    appended: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect surviving parent agents (in order) + appended."""
    result: list[dict[str, Any]] = []
    all_entries = sorted(
        (entry for entries in parent_entries.values() for entry in entries),
        key=lambda e: e[0],
    )
    for _idx, agent, _matched in all_entries:
        if agent is not None:
            result.append(agent)

    # Include parent agents not indexed (safety net).
    indexed_indices = {e[0] for entries in parent_entries.values() for e in entries}
    for idx, agent in enumerate(parent_agents):
        if idx not in indexed_indices:
            result.append(agent)

    result.extend(appended)
    return result


def _merge_departments(
    parent_depts: list[dict[str, Any]],
    child_depts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge department lists by name (case-insensitive).

    Child dept with matching name replaces parent entirely.
    Unmatched child depts are appended.

    Args:
        parent_depts: Parent department dicts.
        child_depts: Child department dicts.

    Returns:
        Merged department list.
    """
    # Index parent depts by lowercase name.
    result: list[dict[str, Any]] = list(parent_depts)
    parent_index: dict[str, int] = {}
    for idx, dept in enumerate(result):
        name = str(dept.get("name", "")).lower()
        if name:
            parent_index[name] = idx

    for child_dept in child_depts:
        name = str(child_dept.get("name", "")).lower()
        if name in parent_index:
            result[parent_index[name]] = child_dept
        else:
            parent_index[name] = len(result)
            result.append(child_dept)

    return result


def _agent_key(agent: dict[str, Any]) -> tuple[str, str]:
    """Compute the merge key for an agent dict."""
    role = str(agent.get("role", "")).lower()
    department = str(agent.get("department", "engineering")).lower()
    return (role, department)
