"""Security feature state slice.

Holds the audit log, the optional trust service (``None`` when the trust
strategy is ``DISABLED``), the autonomy-change strategy, and the optional
process-local red-team report store. The audit log and autonomy strategy are
always wired; controllers raise 503 on a ``None`` field, and the
trust-dependent surface guards on the optional service. The red-team report
store is published here at runtime wiring so the deliverable-receipt builder
can snapshot a run's red-team findings into its receipt.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.security.audit import AuditLog
from synthorg.security.autonomy.protocol import (
    AutonomyChangeStrategy,
)
from synthorg.security.redteam.protocol import RedTeamReportRepository
from synthorg.security.trust.service import TrustService

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class SecurityStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the security feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_log: AuditLog | None = None
    trust_service: TrustService | None = None
    autonomy_change_strategy: AutonomyChangeStrategy | None = None
    red_team_reports: RedTeamReportRepository | None = None


def audit_log_of(app_state: AppStateSliceMixin) -> AuditLog:
    """Resolve the audit log from its slice, or raise 503.

    Returns:
        The wired audit log.
    """
    return require_service(app_state.slice(SecurityStateSlice).audit_log, "Audit Log")


def trust_service_of(app_state: AppStateSliceMixin) -> TrustService:
    """Resolve the trust service from its slice, or raise 503.

    Returns:
        The wired trust service.
    """
    return require_service(
        app_state.slice(SecurityStateSlice).trust_service, "Trust Service"
    )
