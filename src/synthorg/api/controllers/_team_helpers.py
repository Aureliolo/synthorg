"""Controller-side aliases for the shared team-navigation helpers.

The department/team navigation, validation, and settings read/write now live
in the organization layer (:mod:`synthorg.organization.team_navigation`) so the
REST ``TeamController`` and the MCP ``TeamService`` share one implementation and
one write lock. This module re-exports them under the ``_``-prefixed names the
controller already uses.
"""

from synthorg.organization.team_navigation import (
    check_team_name_unique as _check_team_name_unique,
)
from synthorg.organization.team_navigation import find_department as _find_department
from synthorg.organization.team_navigation import find_team as _find_team
from synthorg.organization.team_navigation import member_list as _member_list
from synthorg.organization.team_navigation import (
    persist_company_departments as _persist_departments,
)
from synthorg.organization.team_navigation import persisted_name as _persisted_name
from synthorg.organization.team_navigation import teams_of as _teams_of
from synthorg.organization.team_navigation import (
    validate_team_model as _validate_team_model,
)

__all__ = [
    "_check_team_name_unique",
    "_find_department",
    "_find_team",
    "_member_list",
    "_persist_departments",
    "_persisted_name",
    "_teams_of",
    "_validate_team_model",
]
