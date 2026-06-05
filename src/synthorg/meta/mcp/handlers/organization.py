"""Organization domain MCP handlers.

19 tools across company, departments, teams, and role-version history.
Each handler shims through the corresponding facade on
:class:`AppState`; operations whose underlying primitive cannot satisfy
them surface :class:`CapabilityNotSupportedError` -> typed
``not_supported`` envelope.

The handler bodies live in sibling modules: company in
``organization_company``, departments in ``organization_departments``,
and teams + role versions in ``organization_teams_roles``; the shared
argument / serialisation helpers live in ``_organization_helpers``. This
module aggregates them into the read-only ``ORGANIZATION_HANDLERS`` map.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.organization_company import (
    _company_get,
    _company_list_departments,
    _company_reorder_departments,
    _company_update,
    _company_versions_get,
    _company_versions_list,
)
from synthorg.meta.mcp.handlers.organization_departments import (
    _departments_create,
    _departments_delete,
    _departments_get,
    _departments_get_health,
    _departments_list,
    _departments_update,
)
from synthorg.meta.mcp.handlers.organization_teams_roles import (
    _role_versions_get,
    _role_versions_list,
    _teams_create,
    _teams_delete,
    _teams_get,
    _teams_list,
    _teams_update,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


ORGANIZATION_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_company_get": _company_get,
        "synthorg_company_update": _company_update,
        "synthorg_company_list_departments": _company_list_departments,
        "synthorg_company_reorder_departments": _company_reorder_departments,
        "synthorg_company_versions_list": _company_versions_list,
        "synthorg_company_versions_get": _company_versions_get,
        "synthorg_departments_list": _departments_list,
        "synthorg_departments_get": _departments_get,
        "synthorg_departments_create": _departments_create,
        "synthorg_departments_update": _departments_update,
        "synthorg_departments_delete": _departments_delete,
        "synthorg_departments_get_health": _departments_get_health,
        "synthorg_teams_list": _teams_list,
        "synthorg_teams_get": _teams_get,
        "synthorg_teams_create": _teams_create,
        "synthorg_teams_update": _teams_update,
        "synthorg_teams_delete": _teams_delete,
        "synthorg_role_versions_list": _role_versions_list,
        "synthorg_role_versions_get": _role_versions_get,
    },
)
