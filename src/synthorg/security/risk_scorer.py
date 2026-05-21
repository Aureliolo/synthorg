"""Risk scoring for cumulative risk-unit action budgets.

Provides a multi-dimensional risk assessment model and pluggable scoring
protocol. Each action is scored on four dimensions (0.0--1.0), producing
a scalar ``risk_units`` value via a weighted sum.

See the Operations design page (Risk Budget section).
"""

from types import MappingProxyType
from typing import Final, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger
from synthorg.observability.events.risk_budget import (
    RISK_BUDGET_SCORE_COMPUTED,
    RISK_BUDGET_SCORE_FALLBACK,
    RISK_BUDGET_SCORER_CREATED,
)

logger = get_logger(__name__)


# ── Weights ──────────────────────────────────────────────────────────


class RiskScorerWeights(BaseModel):
    """Weights for the four risk dimensions.

    All weights must be non-negative and sum to 1.0.

    Attributes:
        reversibility: Weight for the reversibility dimension.
        blast_radius: Weight for the blast radius dimension.
        data_sensitivity: Weight for the data sensitivity dimension.
        external_visibility: Weight for the external visibility dimension.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    reversibility: float = Field(default=0.3, ge=0.0, le=1.0)
    blast_radius: float = Field(default=0.3, ge=0.0, le=1.0)
    data_sensitivity: float = Field(default=0.2, ge=0.0, le=1.0)
    external_visibility: float = Field(default=0.2, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_sum(self) -> Self:
        """Ensure weights sum to 1.0 (within floating-point tolerance)."""
        total = (
            self.reversibility
            + self.blast_radius
            + self.data_sensitivity
            + self.external_visibility
        )
        _tolerance = 1e-6
        if abs(total - 1.0) > _tolerance:
            msg = f"Weights must sum to 1.0, got {total:.6f}"
            raise ValueError(msg)
        return self


_DEFAULT_WEIGHTS: Final[RiskScorerWeights] = RiskScorerWeights()


# ── Risk Score ───────────────────────────────────────────────────────


class RiskScore(BaseModel):
    """Multi-dimensional risk assessment for a single action.

    Four float dimensions (0.0--1.0) produce a scalar ``risk_units``
    value via a weighted sum. Reversibility is inverted: 0 means fully
    reversible, 1 means irreversible.

    Attributes:
        reversibility: How irreversible the action is (0=reversible, 1=irreversible).
        blast_radius: Scope of impact (0=none, 1=global).
        data_sensitivity: Sensitivity of data touched (0=public, 1=secret).
        external_visibility: Visibility to external parties (0=internal, 1=public).
        weights: Scoring weights (defaults to equal-ish 0.3/0.3/0.2/0.2).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    reversibility: float = Field(
        ge=0.0,
        le=1.0,
        description="Irreversibility (0=reversible, 1=irreversible)",
    )
    blast_radius: float = Field(
        ge=0.0,
        le=1.0,
        description="Scope of impact",
    )
    data_sensitivity: float = Field(
        ge=0.0,
        le=1.0,
        description="Data sensitivity",
    )
    external_visibility: float = Field(
        ge=0.0,
        le=1.0,
        description="External visibility",
    )
    weights: RiskScorerWeights = Field(
        default_factory=lambda: _DEFAULT_WEIGHTS,
        exclude=True,
        description="Scoring weights (excluded from serialization)",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def risk_units(self) -> float:
        """Weighted sum of all dimensions."""
        return (
            self.reversibility * self.weights.reversibility
            + self.blast_radius * self.weights.blast_radius
            + self.data_sensitivity * self.weights.data_sensitivity
            + self.external_visibility * self.weights.external_visibility
        )


# ── Protocol ─────────────────────────────────────────────────────────


@runtime_checkable
class RiskScorer(Protocol):
    """Pluggable risk scorer protocol.

    Implementations score an action type string and return a
    multi-dimensional ``RiskScore``.
    """

    def score(self, action_type: str) -> RiskScore:
        """Score the given action type.

        Args:
            action_type: The ``category:action`` string.

        Returns:
            Multi-dimensional risk score.
        """
        ...


# ── Default Score Map ────────────────────────────────────────────────

# CRITICAL actions: near-maximum on all dimensions.
_CRITICAL_SCORE: Final[RiskScore] = RiskScore(
    reversibility=0.9,
    blast_radius=0.9,
    data_sensitivity=0.8,
    external_visibility=0.9,
)

# HIGH actions: high on most dimensions.
_HIGH_SCORE: Final[RiskScore] = RiskScore(
    reversibility=0.7,
    blast_radius=0.6,
    data_sensitivity=0.5,
    external_visibility=0.6,
)

# MEDIUM actions: moderate across the board.
_MEDIUM_SCORE: Final[RiskScore] = RiskScore(
    reversibility=0.4,
    blast_radius=0.3,
    data_sensitivity=0.3,
    external_visibility=0.2,
)

# LOW actions: minimal risk.
_LOW_SCORE: Final[RiskScore] = RiskScore(
    reversibility=0.1,
    blast_radius=0.05,
    data_sensitivity=0.05,
    external_visibility=0.0,
)

# Fail-safe fallback for unknown action types.
_UNKNOWN_SCORE: Final[RiskScore] = RiskScore(
    reversibility=0.8,
    blast_radius=0.7,
    data_sensitivity=0.6,
    external_visibility=0.7,
)

_DEFAULT_SCORE_MAP: Final[MappingProxyType[str, RiskScore]] = MappingProxyType(
    {
        # CRITICAL
        ActionType.DEPLOY_PRODUCTION: _CRITICAL_SCORE,
        ActionType.DB_ADMIN: _CRITICAL_SCORE,
        ActionType.ORG_FIRE: _CRITICAL_SCORE,
        # HIGH
        ActionType.DEPLOY_STAGING: _HIGH_SCORE,
        ActionType.DB_MUTATE: _HIGH_SCORE,
        ActionType.CODE_DELETE: _HIGH_SCORE,
        ActionType.VCS_PUSH: _HIGH_SCORE,
        ActionType.COMMS_EXTERNAL: _HIGH_SCORE,
        # External API/data access reaches a third party with brokered
        # credentials; same tier as outbound external comms.
        ActionType.EXTERNAL_DATA_REQUEST: _HIGH_SCORE,
        ActionType.BUDGET_EXCEED: _HIGH_SCORE,
        # Authoring a new tool registers sandboxed code the org will run;
        # governed at HIGH, matching the tool-creation approval gate.
        ActionType.TOOL_CREATE: _HIGH_SCORE,
        # MEDIUM
        ActionType.CODE_CREATE: _MEDIUM_SCORE,
        ActionType.CODE_WRITE: _MEDIUM_SCORE,
        ActionType.CODE_REFACTOR: _MEDIUM_SCORE,
        ActionType.VCS_COMMIT: _MEDIUM_SCORE,
        ActionType.ARCH_DECIDE: _MEDIUM_SCORE,
        # Ingestion pulls external (possibly untrusted) content into the
        # knowledge corpus; admin-gated, moderate blast radius.
        ActionType.KNOWLEDGE_INGEST: _MEDIUM_SCORE,
        ActionType.ORG_HIRE: _MEDIUM_SCORE,
        ActionType.ORG_PROMOTE: _MEDIUM_SCORE,
        ActionType.BUDGET_SPEND: _MEDIUM_SCORE,
        # LOW
        ActionType.CODE_READ: _LOW_SCORE,
        ActionType.VCS_READ: _LOW_SCORE,
        ActionType.TEST_RUN: _LOW_SCORE,
        ActionType.TEST_WRITE: _LOW_SCORE,
        ActionType.DOCS_WRITE: _LOW_SCORE,
        ActionType.VCS_BRANCH: _LOW_SCORE,
        ActionType.COMMS_INTERNAL: _LOW_SCORE,
        ActionType.DB_QUERY: _LOW_SCORE,
        ActionType.MEMORY_READ: _LOW_SCORE,
        # Re-indexing reprocesses already-vetted sources; read-only on the
        # outside world.
        ActionType.KNOWLEDGE_REINDEX: _LOW_SCORE,
        # Browser tool capabilities. The browser runs sandboxed and only
        # observes / probes the workspace's own deliverables (test data),
        # so the tier is LOW.
        ActionType.BROWSER_NAVIGATE: _LOW_SCORE,
        ActionType.BROWSER_SCREENSHOT: _LOW_SCORE,
        ActionType.BROWSER_DIFF: _LOW_SCORE,
        ActionType.BROWSER_ACCESSIBILITY_SCAN: _LOW_SCORE,
        ActionType.BROWSER_SPEC: _LOW_SCORE,
        # Virtual desktop tool capabilities. The desktop runs sandboxed
        # on a headless display and operates the workspace's own GUI
        # deliverable. Launch spawns a process (bash -c) so it is MEDIUM;
        # pointer / keyboard / capture actions only drive the running app.
        ActionType.DESKTOP_LAUNCH: _MEDIUM_SCORE,
        ActionType.DESKTOP_CLICK: _LOW_SCORE,
        ActionType.DESKTOP_TYPE: _LOW_SCORE,
        ActionType.DESKTOP_KEY: _LOW_SCORE,
        ActionType.DESKTOP_SCREENSHOT: _LOW_SCORE,
        ActionType.DESKTOP_SCROLL: _LOW_SCORE,
    }
)

# Exhaustiveness check: ensure every ActionType has an explicit score.
_missing_actions = set(ActionType) - set(_DEFAULT_SCORE_MAP)
if _missing_actions:
    _msg = f"_DEFAULT_SCORE_MAP missing entries for: {_missing_actions}"
    raise RuntimeError(_msg)


# ── Default Implementation ───────────────────────────────────────────


class DefaultRiskScorer:
    """Default risk scorer using a static action-type-to-score map.

    Mirrors the ``RiskClassifier`` risk level mapping but provides
    multi-dimensional ``RiskScore`` values instead of a single
    ``ApprovalRiskLevel`` tier.

    Args:
        custom_scores: Additional or overriding score mappings.
        weights: Custom scoring weights applied to returned scores.
    """

    def __init__(
        self,
        *,
        custom_scores: dict[str, RiskScore] | None = None,
        weights: RiskScorerWeights | None = None,
    ) -> None:
        self._weights = weights or _DEFAULT_WEIGHTS
        if custom_scores:
            merged: dict[str, RiskScore] = dict(_DEFAULT_SCORE_MAP)
            merged.update(custom_scores)
            self._score_map = MappingProxyType(merged)
        else:
            self._score_map = _DEFAULT_SCORE_MAP
        logger.debug(
            RISK_BUDGET_SCORER_CREATED,
            custom_count=len(custom_scores) if custom_scores else 0,
            has_custom_weights=weights is not None,
        )

    def score(self, action_type: str) -> RiskScore:
        """Score the given action type.

        Falls back to a high-risk default for unknown action types
        (fail-safe, matching ``RiskClassifier.classify`` behavior).

        Args:
            action_type: The ``category:action`` string.

        Returns:
            Multi-dimensional risk score with configured weights.
        """
        base_score = self._score_map.get(action_type)
        if base_score is None:
            logger.warning(
                RISK_BUDGET_SCORE_FALLBACK,
                action_type=action_type,
                fallback_risk_units=_UNKNOWN_SCORE.risk_units,
            )
            base_score = _UNKNOWN_SCORE

        # Apply custom weights if different from defaults.
        if self._weights is not _DEFAULT_WEIGHTS:
            result = RiskScore(
                reversibility=base_score.reversibility,
                blast_radius=base_score.blast_radius,
                data_sensitivity=base_score.data_sensitivity,
                external_visibility=base_score.external_visibility,
                weights=self._weights,
            )
        else:
            result = base_score

        logger.debug(
            RISK_BUDGET_SCORE_COMPUTED,
            action_type=action_type,
            risk_units=result.risk_units,
        )
        return result
