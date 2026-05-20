"""Red-team tool surface.

Exposes the single agent-callable tool: ``submit_red_team_report``.
The tool is instantiated per-evaluation by the
:class:`RedTeamGateService`; constructor binds the report repository
and the execution / task identifiers so the agent only needs to
supply the findings + summary in the call.
"""

from synthorg.security.redteam.tools._args import SubmitRedTeamReportArgs
from synthorg.security.redteam.tools.submit_report import (
    SUBMIT_RED_TEAM_REPORT_TOOL_NAME,
    SubmitRedTeamReportTool,
)

__all__ = [
    "SUBMIT_RED_TEAM_REPORT_TOOL_NAME",
    "SubmitRedTeamReportArgs",
    "SubmitRedTeamReportTool",
]
