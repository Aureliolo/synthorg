"""Adversarial red-team subsystem.

A built-in adversarial agent role and the gate that runs it as the
LAST check before a deliverable is marked complete. The gate is
opt-in via :attr:`synthorg.security.config.SecurityConfig.red_team_enabled`
(see also ``RedTeamConfig``).

Layering:

- :mod:`.models` defines the structured findings + report + gate-result
  schemas. Frozen Pydantic v2, ``extra='forbid'``.
- :mod:`.errors` defines :class:`RedTeamError` and subclasses.
- :mod:`.protocol` defines the :class:`AgentRunner`,
  :class:`RedTeamGate`, and :class:`RedTeamReportRepository` protocols.
- :mod:`.routing` computes the severity x autonomy verdict.
- :mod:`.grounding` ships the stub :class:`HeuristicGroundingChecker`
  and the :class:`GroundingChecker` protocol that EPIC E #1988 will
  plug into.
- :mod:`.report_repo` provides the in-memory repo implementation.
- :mod:`.gate` provides :class:`RedTeamGateService`, the inline-
  AgentEngine.run orchestrator.
"""

from synthorg.security.redteam.agent import (
    RED_TEAM_AGENT_NAME,
    build_red_team_agent_identity,
)
from synthorg.security.redteam.builder import (
    RedTeamRuntime,
    RedTeamToolSeed,
    build_red_team_runtime,
    build_red_team_tool_seed,
)
from synthorg.security.redteam.errors import (
    RedTeamDispatchError,
    RedTeamError,
    RedTeamReportAlreadyExistsError,
    RedTeamReportNotFoundError,
    RedTeamReportValidationError,
)
from synthorg.security.redteam.gate import RedTeamGateService
from synthorg.security.redteam.grounding import (
    GroundingChecker,
    HeuristicGroundingChecker,
    UngroundedClaim,
    build_grounding_checker,
)
from synthorg.security.redteam.models import (
    MAX_FINDINGS_PER_REPORT,
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamGateResult,
    RedTeamReport,
    RedTeamReviewInput,
    RedTeamSeverity,
    RedTeamVerdict,
    severity_rank,
)
from synthorg.security.redteam.prompt import build_red_team_system_prompt
from synthorg.security.redteam.protocol import (
    AgentRunner,
    RedTeamGate,
    RedTeamReportRepository,
)
from synthorg.security.redteam.report_repo import InMemoryRedTeamReportRepository
from synthorg.security.redteam.routing import (
    compute_red_team_verdict,
    should_block,
)

__all__ = [
    "MAX_FINDINGS_PER_REPORT",
    "RED_TEAM_AGENT_NAME",
    "AgentRunner",
    "GroundingChecker",
    "HeuristicGroundingChecker",
    "InMemoryRedTeamReportRepository",
    "RedTeamAttackSurface",
    "RedTeamDispatchError",
    "RedTeamError",
    "RedTeamFinding",
    "RedTeamGate",
    "RedTeamGateResult",
    "RedTeamGateService",
    "RedTeamReport",
    "RedTeamReportAlreadyExistsError",
    "RedTeamReportNotFoundError",
    "RedTeamReportRepository",
    "RedTeamReportValidationError",
    "RedTeamReviewInput",
    "RedTeamRuntime",
    "RedTeamSeverity",
    "RedTeamToolSeed",
    "RedTeamVerdict",
    "UngroundedClaim",
    "build_grounding_checker",
    "build_red_team_agent_identity",
    "build_red_team_runtime",
    "build_red_team_system_prompt",
    "build_red_team_tool_seed",
    "compute_red_team_verdict",
    "severity_rank",
    "should_block",
]
