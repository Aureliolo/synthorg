"""Template config merging for inheritance.

Provides ``merge_template_configs`` which combines a parent config dict
with a child config dict, implementing the merge semantics described in
the template inheritance design.
"""

import copy
from dataclasses import dataclass, field
from typing import Any

from synthorg.config.utils import deep_merge
from synthorg.observability import get_logger
from synthorg.observability.events.template import (
    TEMPLATE_INHERIT_MERGE,
    TEMPLATE_INHERIT_MERGE_ERROR,
)
from synthorg.templates.errors import TemplateInheritanceError

logger = get_logger(__name__)

# Single source of truth for the default department.
# renderer.py re-imports this value for its own use.
DEFAULT_MERGE_DEPARTMENT = "engineering"

_STRIP_KEYS: frozenset[str] = frozenset({"merge_id", "_remove"})


@dataclass
class _ParentEntry:
    """Tracking record for a parent agent during merge."""

    index: int
    agent: dict[str, Any] | None
    matched: bool = field(default=False)


def merge_template_configs(
    parent: dict[str, Any],
    child: dict[str, Any],
) -> dict[str, Any]:
    """Merge a parent config dict with a child config dict.

    Merge strategies by field:

    - ``company_name``, ``company_type``: child wins if present.
    - ``config`` (dict): deep-merged; child keys override parent.
    - ``agents`` (list): merged by ``(role, department, merge_id)`` key.
    - ``departments`` (list): merged by ``name`` (case-insensitive).
    - ``workflow_handoffs``, ``escalation_paths``: child replaces
      entirely if present.

    Args:
        parent: Rendered parent config dict (post-Jinja2, pre-defaults).
        child: Rendered child config dict (post-Jinja2, pre-defaults).

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

    # Agents: merge by (role, department, merge_id) key.
    parent_agents = parent.get("agents", [])
    child_agents = child.get("agents", [])
    if parent_agents or child_agents:
        result["agents"] = _merge_agents(parent_agents, child_agents)

    # Departments: merge by name.
    parent_depts = parent.get("departments", [])
    child_depts = child.get("departments", [])
    if parent_depts or child_depts:
        result["departments"] = _merge_departments(parent_depts, child_depts)

    # Replace-if-present fields (deep-copied to prevent reference sharing).
    for key in ("workflow_handoffs", "escalation_paths"):
        if key in child and child[key] is not None:
            result[key] = copy.deepcopy(child[key])
        elif key in parent:
            result[key] = copy.deepcopy(parent[key])

    logger.debug(TEMPLATE_INHERIT_MERGE, action="done")
    return result


def _merge_agents(
    parent_agents: list[dict[str, Any]],
    child_agents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge agent lists by ``(role, department, merge_id)`` key.

    Algorithm:
    1. Index parent agents by ``(role.lower(), department.lower(),
       merge_id.lower())``.  Duplicate keys maintain an ordered list
       per key.
    2. Walk child agents:
       - ``_remove: true``: find first unmatched parent with same key,
         remove it. Child entry is discarded.
       - Otherwise: match against first unmatched parent with same key,
         replace. No match -> append.
    3. Discard ``_remove`` entries; strip ``_remove`` and ``merge_id``
       keys from replacement/appended dicts.
    4. Result: parent agents (with replacements/removals) + appended.

    Args:
        parent_agents: Parent agent dicts, each expected to have at
            least ``role`` and optionally ``department`` keys.
        child_agents: Child agent dicts; may include ``_remove: True``
            to remove a matching parent agent.

    Returns:
        Merged agent list.

    Raises:
        TemplateInheritanceError: If ``_remove`` has no matching parent.
    """
    parent_entries: dict[tuple[str, str, str], list[_ParentEntry]] = {}
    for idx, agent in enumerate(parent_agents):
        key = _agent_key(agent)
        parent_entries.setdefault(key, []).append(
            _ParentEntry(index=idx, agent=copy.deepcopy(agent)),
        )

    appended: list[dict[str, Any]] = []
    for child_agent in child_agents:
        _apply_child_agent(child_agent, parent_entries, appended)

    return _collect_merged_agents(parent_entries, appended)


def _apply_child_agent(
    child_agent: dict[str, Any],
    parent_entries: dict[tuple[str, str, str], list[_ParentEntry]],
    appended: list[dict[str, Any]],
) -> None:
    """Apply a single child agent against parent entries.

    Updates *parent_entries* and *appended* as a local mutation
    scoped to the enclosing ``_merge_agents`` call.
    """
    key = _agent_key(child_agent)
    is_remove = child_agent.get("_remove", False)
    entries = parent_entries.get(key, [])

    matched_entry = _find_unmatched(entries)

    if is_remove:
        if matched_entry is None:
            msg = f"Cannot remove agent with key {key}: no matching parent agent found"
            logger.error(
                TEMPLATE_INHERIT_MERGE_ERROR,
                action="remove_failed",
                key=key,
            )
            raise TemplateInheritanceError(msg)
        matched_entry.matched = True
        matched_entry.agent = None  # mark for removal
        return

    clean = copy.deepcopy(
        {k: v for k, v in child_agent.items() if k not in _STRIP_KEYS}
    )

    if matched_entry is not None:
        matched_entry.matched = True
        matched_entry.agent = clean
    else:
        appended.append(clean)


def _find_unmatched(
    entries: list[_ParentEntry],
) -> _ParentEntry | None:
    """Find first unmatched entry in a parent entries list."""
    return next((e for e in entries if not e.matched), None)


def _collect_merged_agents(
    parent_entries: dict[tuple[str, str, str], list[_ParentEntry]],
    appended: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect surviving parent agents (in order) + appended."""
    all_entries = sorted(
        (entry for entries in parent_entries.values() for entry in entries),
        key=lambda e: e.index,
    )
    result: list[dict[str, Any]] = [
        {k: v for k, v in entry.agent.items() if k not in _STRIP_KEYS}
        for entry in all_entries
        if entry.agent is not None
    ]
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
    # Build child overrides index (skip nameless departments).
    child_by_name: dict[str, dict[str, Any]] = {}
    nameless_child: list[dict[str, Any]] = []
    for child_dept in child_depts:
        name = str(child_dept.get("name", "")).lower()
        if not name:
            logger.warning(
                TEMPLATE_INHERIT_MERGE,
                action="department_no_name",
            )
            nameless_child.append(child_dept)
            continue
        child_by_name[name] = copy.deepcopy(child_dept)

    # Walk parent depts: apply child override if it exists.
    result: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for dept in parent_depts:
        name = str(dept.get("name", "")).lower()
        if not name:
            logger.warning(
                TEMPLATE_INHERIT_MERGE,
                action="department_no_name",
            )
            result.append(copy.deepcopy(dept))
            continue
        if name in child_by_name:
            result.append(child_by_name[name])
            seen_names.add(name)
        else:
            result.append(copy.deepcopy(dept))
            seen_names.add(name)

    # Append unmatched child depts + nameless children.
    for name, child_dept in child_by_name.items():
        if name not in seen_names:
            result.append(child_dept)
    result.extend(copy.deepcopy(nameless_child))

    return result


def _agent_key(agent: dict[str, Any]) -> tuple[str, str, str]:
    """Compute the merge key for an agent dict.

    Uses ``(role, department, merge_id)`` when ``merge_id`` is present,
    otherwise ``(role, department, "")`` for backwards compatibility.
    """
    role = str(agent.get("role", "")).lower()
    if not role:
        msg = f"Agent dict is missing 'role' field (keys: {sorted(agent.keys())})"
        logger.error(
            TEMPLATE_INHERIT_MERGE_ERROR,
            action="missing_role",
            agent_keys=sorted(agent.keys()),
            error=msg,
        )
        raise TemplateInheritanceError(msg)
    dept = agent.get("department")
    if not dept:
        dept = DEFAULT_MERGE_DEPARTMENT
    merge_id = str(agent.get("merge_id", "")).lower()
    return (role, str(dept).lower(), merge_id)
