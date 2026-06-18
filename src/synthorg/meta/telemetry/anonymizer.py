"""Anonymization functions for cross-deployment analytics.

Pure functions that transform rich domain models into anonymized
events suitable for cross-deployment aggregation. Only allowlisted
fields survive: enum values, numeric metrics, and coarsened
timestamps. All free text, UUIDs, and PII are dropped.
"""

import hashlib
import hmac
from collections.abc import Collection
from datetime import datetime
from typing import Final

from synthorg import __version__
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import ProposalOutcome
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import ImprovementProposal, RolloutResult
from synthorg.meta.telemetry.config import CrossDeploymentAnalyticsConfig
from synthorg.meta.telemetry.models import AnonymizedOutcomeEvent


def anonymize_decision(
    outcome: ProposalOutcome,
    *,
    analytics_config: CrossDeploymentAnalyticsConfig,
    self_improvement_config: SelfImprovementConfig,
    builtin_rule_names: Collection[str],
) -> AnonymizedOutcomeEvent:
    """Anonymize a proposal decision into a cross-deployment event.

    Drops all PII (decided_by, decision_reason, title, proposal_id).
    Keeps only categorical enums, numeric metrics, and coarsened
    timestamps.

    Args:
        outcome: The proposal outcome to anonymize.
        analytics_config: Cross-deployment analytics configuration.
        self_improvement_config: Full self-improvement config (for
            extracting enabled altitudes).
        builtin_rule_names: Set of built-in rule names. Custom
            rules are masked to ``"custom"``.

    Returns:
        Anonymized event with ``event_type="proposal_decision"``.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if analytics_config.deployment_id_salt is None:
        msg = "deployment_id_salt is required for anonymization"
        raise ValueError(msg)
    return AnonymizedOutcomeEvent(
        deployment_id=NotBlankStr(
            _compute_deployment_id(str(analytics_config.deployment_id_salt)),
        ),
        event_type="proposal_decision",
        timestamp=NotBlankStr(_coarsen_timestamp(outcome.decided_at)),
        altitude=NotBlankStr(outcome.altitude.value),
        source_rule=_classify_rule(
            outcome.source_rule,
            builtin_rule_names,
        ),
        decision=outcome.decision,
        confidence=outcome.confidence_at_decision,
        rollout_outcome=None,
        regression_verdict=None,
        observation_hours=None,
        enabled_altitudes=_collect_enabled_altitudes(self_improvement_config),
        industry_tag=analytics_config.industry_tag,
        sdk_version=NotBlankStr(__version__),
    )


def anonymize_rollout(
    result: RolloutResult,
    *,
    proposal: ImprovementProposal,
    analytics_config: CrossDeploymentAnalyticsConfig,
    self_improvement_config: SelfImprovementConfig,
    builtin_rule_names: Collection[str],
) -> AnonymizedOutcomeEvent:
    """Anonymize a rollout result into a cross-deployment event.

    Drops free-text details and raw proposal_id. Keeps rollout
    outcome enum, regression verdict, and observation duration.

    Args:
        result: The rollout result to anonymize.
        proposal: The associated proposal (for altitude/rule context).
        analytics_config: Cross-deployment analytics configuration.
        self_improvement_config: Full self-improvement config.
        builtin_rule_names: Set of built-in rule names.

    Returns:
        Anonymized event with ``event_type="rollout_result"``.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if analytics_config.deployment_id_salt is None:
        msg = "deployment_id_salt is required for anonymization"
        raise ValueError(msg)
    return AnonymizedOutcomeEvent(
        deployment_id=NotBlankStr(
            _compute_deployment_id(str(analytics_config.deployment_id_salt)),
        ),
        event_type="rollout_result",
        timestamp=NotBlankStr(
            _coarsen_timestamp(result.completed_at),
        ),
        altitude=NotBlankStr(proposal.altitude.value),
        source_rule=_classify_rule(
            proposal.source_rule,
            builtin_rule_names,
        ),
        decision=None,
        confidence=None,
        rollout_outcome=NotBlankStr(result.outcome.value),
        regression_verdict=(
            NotBlankStr(result.regression_verdict.value)
            if result.regression_verdict is not None
            else None
        ),
        observation_hours=result.observation_hours_elapsed,
        enabled_altitudes=_collect_enabled_altitudes(self_improvement_config),
        industry_tag=analytics_config.industry_tag,
        sdk_version=NotBlankStr(__version__),
    )


_DEPLOYMENT_ID_HMAC_LABEL: Final[bytes] = b"synthorg.telemetry.deployment_id"


def _compute_deployment_id(salt: str) -> str:
    """Derive a stable deployment id from the deployment salt via HMAC.

    The salt keys an HMAC-SHA256 over a fixed domain-separation label
    rather than being hashed directly. A bare ``sha256(salt)`` is
    brute-forceable offline when the salt has low entropy (a guessable
    deployment name); the HMAC construction forces an attacker to know
    the salt itself to reproduce the id, so it stays a one-way, salt-
    keyed correlation token that never exposes the salt.

    The result is deterministic for a given salt, enabling cross-event
    correlation within a deployment.

    Args:
        salt: Deployment-specific secret salt string (the HMAC key).

    Returns:
        64-character lowercase hex HMAC-SHA256 digest.
    """
    return hmac.new(
        salt.encode("utf-8"),
        _DEPLOYMENT_ID_HMAC_LABEL,
        hashlib.sha256,
    ).hexdigest()


def _classify_rule(
    rule_name: str | None,
    builtin_names: Collection[str],
) -> NotBlankStr | None:
    """Classify a rule name for anonymized output.

    Built-in rule names pass through. Custom rule names are
    replaced with ``"custom"`` to prevent leaking deployment-
    specific rule logic.

    Args:
        rule_name: Original rule name (may be None).
        builtin_names: Set of known built-in rule names.

    Returns:
        Classified rule name or None.
    """
    if rule_name is None:
        return None
    if rule_name in builtin_names:
        return NotBlankStr(rule_name)
    return NotBlankStr("custom")


def _coarsen_timestamp(dt: datetime) -> str:
    """Coarsen a datetime to day-granularity ISO date string.

    Strips time, timezone, and sub-day precision to prevent
    timing-based correlation attacks.

    Args:
        dt: Aware datetime to coarsen.

    Returns:
        ISO 8601 date string (e.g., ``"2026-04-16"``).
    """
    return dt.date().isoformat()


def _collect_enabled_altitudes(
    config: SelfImprovementConfig,
) -> tuple[NotBlankStr, ...]:
    """Extract enabled altitude names from config.

    Returns only categorical names, not the actual config values.

    Args:
        config: Self-improvement configuration.

    Returns:
        Tuple of enabled altitude name strings.
    """
    altitudes: list[NotBlankStr] = []
    if config.config_tuning_enabled:
        altitudes.append(NotBlankStr("config_tuning"))
    if config.architecture_proposals_enabled:
        altitudes.append(NotBlankStr("architecture"))
    if config.prompt_tuning_enabled:
        altitudes.append(NotBlankStr("prompt_tuning"))
    if config.code_modification_enabled:
        altitudes.append(NotBlankStr("code_modification"))
    return tuple(altitudes)
