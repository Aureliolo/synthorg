# module-kind: code
"""Boot wiring for the SecOps risk-tier override subsystem.

The tiered approval-timeout policy is the only consumer of the risk
classifier, so runtime SecOps overrides only matter when a
:class:`TieredTimeoutConfig` is configured. Once persistence is up this
hook rebuilds the tiered policy's classifier wrapped in a
:class:`SecOpsRiskClassifier` seeded from the durable override repo, swaps
the wrapping tiered policy into the live scheduler via
``ApprovalTimeoutScheduler.set_timeout_policy`` (which holds the scheduler's
``_lifecycle_lock`` to order the swap against any concurrent ``start`` /
``stop``), and publishes a :class:`RiskOverrideService` so the REST + MCP
surfaces can create / revoke overrides that mutate the same live classifier.

Best-effort + idempotent: an already-wired service short-circuits, and a
non-tiered policy / absent persistence / absent scheduler leaves the
service unwired (its controllers + handlers honestly 503).
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.security.rules.risk_override import SecOpsRiskClassifier
from synthorg.security.rules.risk_override_service import RiskOverrideService
from synthorg.security.state import SecurityStateSlice
from synthorg.security.timeout.config import ApprovalTimeoutConfig, TieredTimeoutConfig
from synthorg.security.timeout.policies import TieredTimeoutPolicy
from synthorg.security.timeout.risk_classifier_config import RiskClassifierDeps
from synthorg.security.timeout.risk_classifier_factory import (
    build_risk_tier_classifier,
)
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler

logger = get_logger(__name__)


async def wire_risk_override_service(
    app_state: AppState,
    *,
    approval_timeout_config: ApprovalTimeoutConfig | None,
    approval_timeout_scheduler: ApprovalTimeoutScheduler | None,
) -> None:
    """Wire the SecOps risk-override service + live classifier at startup.

    Args:
        app_state: The application state holding the collaborator slices.
        approval_timeout_config: The resolved approval-timeout config; the
            override subsystem only wires for a tiered policy.
        approval_timeout_scheduler: The background timeout scheduler whose
            policy classifier is hot-swapped to the override-aware one.
    """
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    if app_state.slice(SecurityStateSlice).risk_override_service is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    if approval_timeout_scheduler is None:
        return
    if not isinstance(approval_timeout_config, TieredTimeoutConfig):
        return
    try:
        await _wire(app_state, approval_timeout_config, approval_timeout_scheduler)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="risk_override",
            note="risk-override wiring failed; overrides stay unavailable",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire(
    app_state: AppState,
    config: TieredTimeoutConfig,
    scheduler: ApprovalTimeoutScheduler,
) -> None:
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    repo = persistence_of(app_state).risk_overrides
    base = build_risk_tier_classifier(config.risk_classifier, RiskClassifierDeps())
    active = await repo.list_active()
    secops = SecOpsRiskClassifier(
        base=base,
        overrides=active,
        clock=app_state.clock,
    )
    await scheduler.set_timeout_policy(
        TieredTimeoutPolicy(tiers=config.tiers, classifier=secops),
    )
    service = RiskOverrideService(
        repo=repo,
        classifier=secops,
        base_classifier=base,
        clock=app_state.clock,
    )
    app_state.wire(SecurityStateSlice, risk_override_service=service)
    logger.info(
        API_APP_STARTUP,
        service="risk_override",
        note="wired",
        active_overrides=len(active),
    )


__all__ = ["wire_risk_override_service"]
