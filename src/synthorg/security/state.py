"""Security feature state slice.

Holds the audit log, the autonomy-change strategy, and the optional
process-local red-team report store. The audit log and autonomy strategy are
always wired; controllers raise 503 on a ``None`` field. The red-team report
store is published here at runtime wiring so the deliverable-receipt builder
can snapshot a run's red-team findings into its receipt.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.security.audit import AuditLog
from synthorg.security.autonomy.protocol import (
    AutonomyChangeStrategy,
)
from synthorg.security.policy_engine.protocol import PolicyEngine
from synthorg.security.redteam.protocol import RedTeamReportRepository
from synthorg.security.rules.risk_override_service import RiskOverrideService


class SecurityStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the security feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    audit_log: AuditLog | None = None
    autonomy_change_strategy: AutonomyChangeStrategy | None = None
    red_team_reports: RedTeamReportRepository | None = None
    policy_engine: PolicyEngine | None = None
    risk_override_service: RiskOverrideService | None = None


def audit_log_of(app_state: AppStateSliceMixin) -> AuditLog:
    """Resolve the audit log from its slice, or raise 503.

    Returns:
        The wired audit log.
    """
    return require_service(app_state.slice(SecurityStateSlice).audit_log, "Audit Log")


def risk_override_service_of(app_state: AppStateSliceMixin) -> RiskOverrideService:
    """Resolve the SecOps risk-override service from its slice, or raise 503.

    Absent unless the configured approval-timeout policy is tiered (the
    only consumer of the risk classifier the overrides drive).

    Returns:
        The wired risk-override service.
    """
    return require_service(
        app_state.slice(SecurityStateSlice).risk_override_service,
        "Risk Override Service",
    )
