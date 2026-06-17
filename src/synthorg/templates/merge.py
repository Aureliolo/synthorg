"""Template config merging for inheritance.

Provides ``merge_template_configs`` which combines a parent config dict
with a child config dict, implementing the merge semantics described in
the template inheritance design.
"""

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

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
_DEPT_STRIP_KEYS: frozenset[str] = frozenset({"_remove"})


@dataclass
class _ParentEntry:
    """Tracking record for a parent agent during merge."""

    index: int
    agent: dict[str, object] | None
    matched: bool = field(default=False)


def _as_dict_list(value: object, field_name: str) -> list[dict[str, object]]:
    """Narrow a rendered-config value to a list of mapping records.

    Args:
        value: Candidate value pulled from a rendered config dict.
        field_name: Field name for error context.

    Returns:
        The value as a list of mapping records (dict-ness is enforced;
        keys are assumed to be strings per the rendered-config contract).

    Raises:
        TemplateInheritanceError: When *value* is not a list, or any
            element is not a mapping.
    """
    if not isinstance(value, list):
        msg = f"Merged template {field_name!r} must be a list"
        logger.error(
            TEMPLATE_INHERIT_MERGE_ERROR,
            action="invalid_field_type",
            field=field_name,
        )
        raise TemplateInheritanceError(msg)
    records: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            msg = f"Merged template {field_name!r} entries must be mappings"
            logger.error(
                TEMPLATE_INHERIT_MERGE_ERROR,
                action="invalid_field_entry",
                field=field_name,
            )
            raise TemplateInheritanceError(msg)
        records.append(item)
    return records


def merge_template_configs(
    parent: dict[str, object],
    child: dict[str, object],
) -> dict[str, object]:
    """Merge a parent config dict with a child config dict.

    Merge strategies by field:

    - ``company_name``, ``company_type``: child wins if present.
    - ``config``, ``security``, ``budget`` (dict): deep-merged; child keys
      override parent.
    - ``agents`` (list): merged by ``(role, department, merge_id)`` key.
    - ``departments`` (list): merged by ``name`` (case-insensitive).
    - ``workflow``, ``workflow_handoffs``, ``escalation_paths``, ``posture``:
      child replaces entirely if present; otherwise inherited from parent.

    Args:
        parent: Rendered parent config dict (post-Jinja2, pre-defaults).
        child: Rendered child config dict (post-Jinja2, pre-defaults).

    Returns:
        New merged config dict.
    """
    logger.debug(TEMPLATE_INHERIT_MERGE, action="start")

    result: dict[str, object] = {}

    # Scalars: child wins if present.
    for key in ("company_name", "company_type"):
        if key in child and child[key] is not None:
            result[key] = child[key]
        elif key in parent:
            result[key] = parent[key]

    # Deep-merged dict sections: child keys override parent.
    for section in ("config", "security", "budget"):
        parent_section = parent.get(section, {})
        child_section = child.get(section, {})
        if parent_section or child_section:
            result[section] = deep_merge(
                parent_section if isinstance(parent_section, Mapping) else {},
                child_section if isinstance(child_section, Mapping) else {},
            )

    # Agents: merge by (role, department, merge_id) key.
    parent_agents = parent.get("agents", [])
    child_agents = child.get("agents", [])
    if parent_agents or child_agents:
        result["agents"] = _merge_agents(
            _as_dict_list(parent_agents, "agents"),
            _as_dict_list(child_agents, "agents"),
        )

    # Departments: merge by name.
    parent_depts = parent.get("departments", [])
    child_depts = child.get("departments", [])
    if parent_depts or child_depts:
        result["departments"] = _merge_departments(
            _as_dict_list(parent_depts, "departments"),
            _as_dict_list(child_depts, "departments"),
        )

    # Replace-if-present fields (deep-copied to prevent reference sharing).
    # ``posture`` is child-wins: a child template's declared posture replaces
    # the parent's entirely rather than blending two operating modes.
    for key in ("workflow", "workflow_handoffs", "escalation_paths", "posture"):
        if key in child and child[key] is not None:
            result[key] = copy.deepcopy(child[key])
        elif key in parent:
            result[key] = copy.deepcopy(parent[key])

    logger.debug(TEMPLATE_INHERIT_MERGE, action="done")
    return result


def _merge_agents(
    parent_agents: Sequence[Mapping[str, object]],
    child_agents: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
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
    parents = [dict(agent) for agent in parent_agents]
    children = [dict(agent) for agent in child_agents]

    parent_entries: dict[tuple[str, str, str], list[_ParentEntry]] = {}
    for idx, agent in enumerate(parents):
        key = _agent_key(agent)
        parent_entries.setdefault(key, []).append(
            _ParentEntry(index=idx, agent=copy.deepcopy(agent)),
        )

    appended: list[dict[str, object]] = []
    for child_agent in children:
        _apply_child_agent(child_agent, parent_entries, appended)

    return _collect_merged_agents(parent_entries, appended)


def _apply_child_agent(
    child_agent: dict[str, object],
    parent_entries: dict[tuple[str, str, str], list[_ParentEntry]],
    appended: list[dict[str, object]],
) -> None:
    """Apply a single child agent against parent entries.

    Updates *parent_entries* and *appended* as a local mutation
    scoped to the enclosing ``_merge_agents`` call.

    Raises:
        TemplateInheritanceError: When a ``_remove`` directive matches no
            parent agent.
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
    """Find first unmatched entry in a parent entries list.

    Returns:
        The first entry not yet matched, or ``None`` when all are matched.
    """
    return next((e for e in entries if not e.matched), None)


def _collect_merged_agents(
    parent_entries: dict[tuple[str, str, str], list[_ParentEntry]],
    appended: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Collect surviving parent agents (in order) + appended.

    Returns:
        The surviving parent agents in original order, followed by the
        appended child agents.
    """
    all_entries = sorted(
        (entry for entries in parent_entries.values() for entry in entries),
        key=lambda e: e.index,
    )
    result: list[dict[str, object]] = []
    for entry in all_entries:
        agent = entry.agent
        if agent is None:
            continue
        result.append({k: v for k, v in agent.items() if k not in _STRIP_KEYS})
    result.extend(appended)
    return result


def _merge_departments(
    parent_depts: Sequence[Mapping[str, object]],
    child_depts: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Merge department lists by name (case-insensitive).

    Child dept with matching name replaces parent entirely.
    Child dept with ``_remove: true`` removes the matching parent.
    Unmatched child depts are appended.

    Args:
        parent_depts: Parent department dicts.
        child_depts: Child department dicts.

    Returns:
        Merged department list.

    Raises:
        TemplateInheritanceError: If ``_remove`` has no matching parent.
    """
    parents = [dict(dept) for dept in parent_depts]
    children = [dict(dept) for dept in child_depts]

    # Build child overrides index (skip nameless departments).
    child_by_name: dict[str, dict[str, object]] = {}
    nameless_child: list[dict[str, object]] = []
    for child_dept in children:
        name = str(child_dept.get("name", "")).lower()
        if not name:
            logger.warning(
                TEMPLATE_INHERIT_MERGE,
                action="department_no_name",
            )
            nameless_child.append(child_dept)
            continue
        child_by_name[name] = copy.deepcopy(child_dept)

    # Walk parent depts: apply child override/removal if it exists.
    result: list[dict[str, object]] = []
    seen_names: set[str] = set()
    for dept in parents:
        name = str(dept.get("name", "")).lower()
        if not name:
            logger.warning(
                TEMPLATE_INHERIT_MERGE,
                action="department_no_name",
            )
            result.append(copy.deepcopy(dept))
            continue
        if name in child_by_name:
            child_dept = child_by_name[name]
            if child_dept.get("_remove"):
                # Remove: skip this parent department entirely.
                seen_names.add(name)
            else:
                # Override: use child version, strip _remove if present.
                clean = {
                    k: v for k, v in child_dept.items() if k not in _DEPT_STRIP_KEYS
                }
                result.append(clean)
                seen_names.add(name)
        else:
            result.append(copy.deepcopy(dept))
            seen_names.add(name)

    # Append unmatched child depts + nameless children.
    result.extend(_collect_unmatched_child_depts(child_by_name, seen_names))
    result.extend(copy.deepcopy(nameless_child))

    return result


def _collect_unmatched_child_depts(
    child_by_name: dict[str, dict[str, object]],
    seen_names: set[str],
) -> list[dict[str, object]]:
    """Return child departments that didn't match any parent.

    Returns:
        List of cleaned child department dicts.

    Raises:
        TemplateInheritanceError: If a child ``_remove`` has no parent.
    """
    result: list[dict[str, object]] = []
    for name, child_dept in child_by_name.items():
        if name not in seen_names:
            if child_dept.get("_remove"):
                msg = (
                    f"Cannot remove department {name!r}: "
                    "no matching parent department found"
                )
                logger.error(
                    TEMPLATE_INHERIT_MERGE_ERROR,
                    action="department_remove_failed",
                    department=name,
                )
                raise TemplateInheritanceError(msg)
            clean = {k: v for k, v in child_dept.items() if k not in _DEPT_STRIP_KEYS}
            result.append(clean)
    return result


def _agent_key(agent: dict[str, object]) -> tuple[str, str, str]:
    """Compute the merge key for an agent dict.

    Uses ``(role, department, merge_id)`` when ``merge_id`` is present,
    otherwise ``(role, department, "")`` as the default.

    Returns:
        The ``(role, department, merge_id)`` merge key, lower-cased.

    Raises:
        TemplateInheritanceError: When the agent dict has no ``role``.
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
    merge_id = str(agent.get("merge_id") or "").lower()
    return (role, str(dept).lower(), merge_id)
