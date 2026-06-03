"""Pre-flight cost forecast service.

:class:`CostForecaster` produces a calibrated estimate
``(estimated_cost, lower_bound, upper_bound)`` for a brief and a
role-skeleton, before the work pipeline commits any spend. The
operator approves the forecast (and an optional per-run hard ceiling)
in the dashboard; the work-entry adapter consults the persisted
:class:`~synthorg.budget.forecast_models.Forecast` row to decide
whether to dispatch.

The estimator blends a tier-static prior (operator-configurable per
model tier) with per-role historical observations using a Bayesian
shrinkage estimator. With ``n=0`` observations the blend collapses to
the static prior; with ``n -> infinity`` it pulls toward the historical
mean. The uncertainty band is a coefficient-of-variation envelope
around the blended mean; it widens when history is thin and tightens
as observations accumulate.

Brief identity is captured by :func:`compute_brief_hash`: a SHA-256
hex digest of canonical JSON of
``(brief_text, role_skeleton, model_assignments, currency)``. Editing
any of those components produces a fresh hash; the repo's
partial-unique index on ``(brief_hash) WHERE decision='pending'``
prevents two pending rows from existing simultaneously for the same
brief.
"""

import asyncio
import hashlib
import json
import math
import statistics
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Final
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget._cost_window import ClockFn, tier_from_model_id, utc_now
from synthorg.budget.config import BudgetConfig
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.normalization import normalize_identifier
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_FORECAST_GENERATED,
    BUDGET_PREFLIGHT_ERROR,
)

logger = get_logger(__name__)

# Cold-start uncertainty band width as a fraction of the static prior.
# When no historical observations exist the lower / upper bounds span
# +/- this fraction of the point estimate; calibrated against the
# observed dispersion of cost-per-turn across early SynthOrg runs.
_COLD_START_BAND_FRACTION: Final[float] = 0.40

# Lower floor on the warm-band coefficient of variation; the band
# never tightens below this no matter how many historical observations
# accumulate, because LLM provider price + token-mix variance retains
# a floor.
_WARM_BAND_FLOOR_COEFFICIENT: Final[float] = 0.15

# Default turns-per-role estimate used when the brief does not specify
# a per-role turn budget. A typical work item runs 6 to 10 turns; we
# pick the midpoint as the planning default.
_DEFAULT_TURNS_PER_ROLE: Final[float] = 8.0


HistoryLookup = Callable[[str, "NotBlankStr"], Awaitable[Sequence[float]]]
"""Lookup of historical per-turn cost observations keyed by (tier, role_id)."""


async def _empty_history(_tier: str, _role_id: str) -> Sequence[float]:
    """Default :class:`HistoryLookup` returning no observations.

    Returns:
        Result of type ``Sequence[float]``.
    """
    return ()


class BriefSignal(BaseModel):
    """Compact, deterministic description of a brief used by the forecaster.

    The fields are exactly what the estimator needs and what
    :func:`compute_brief_hash` hashes into the canonical
    ``brief_hash``: the brief text (verbatim), the ordered role
    skeleton, the per-role model-tier assignment, and the currency
    the estimate should be denominated in.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    brief_text: NotBlankStr = Field(description="The brief as written by the operator")
    role_skeleton: tuple[NotBlankStr, ...] = Field(
        description="Ordered role ids participating in the run",
    )
    model_assignments: Mapping[NotBlankStr, NotBlankStr] = Field(
        default_factory=dict,
        description="Optional per-role model id (canonical alias)",
    )
    currency: NotBlankStr = Field(description="ISO 4217 code for the estimate")
    estimated_turns_per_role: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional per-role turn estimate; defaults to "
            "_DEFAULT_TURNS_PER_ROLE when omitted"
        ),
    )


def compute_brief_hash(signal: BriefSignal) -> str:
    """Return the canonical SHA-256 hex digest for a brief signal.

    Canonicalisation rules: keys sorted, no whitespace separators,
    role names lower-cased and stripped, model ids passed through
    verbatim (the caller is expected to normalise model ids to their
    canonical alias before constructing the signal).

    Returns:
        Result of type ``str``.
    """
    payload = {
        "brief_text": signal.brief_text,
        "role_skeleton": [normalize_identifier(r) for r in signal.role_skeleton],
        "model_assignments": {
            normalize_identifier(k): v.strip()
            for k, v in sorted(signal.model_assignments.items())
        },
        "currency": signal.currency,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _static_prior_per_turn(config: BudgetConfig, tier: str) -> float:
    """Look up the static prior cost-per-turn for a model tier.

    Returns:
        Result of type ``float``.
    """
    if tier == "large":
        return config.forecast_static_prior_per_turn_large
    if tier == "medium":
        return config.forecast_static_prior_per_turn_medium
    if tier == "small":
        return config.forecast_static_prior_per_turn_small
    if tier == "local-small":
        return config.forecast_static_prior_per_turn_local_small
    return config.forecast_static_prior_per_turn_medium


def _bayesian_blend(
    *,
    prior_mean: float,
    prior_weight: float,
    observations: Sequence[float],
) -> tuple[float, float]:
    """Blend a static prior with observations under Bayesian shrinkage.

    Returns a ``(point_estimate, std_dev)`` tuple. The standard
    deviation is the within-sample standard deviation of the
    observations when there are >= 2 observations, otherwise the
    cold-start width is folded in at the caller's discretion.

    Returns:
        Tuple ``(float, float)``.
    """
    n = len(observations)
    if n == 0:
        return prior_mean, 0.0
    historical_mean = statistics.fmean(observations)
    blended = (prior_weight * prior_mean + n * historical_mean) / (prior_weight + n)
    std = statistics.stdev(observations) if n >= 2 else 0.0  # noqa: PLR2004
    return blended, std


class CostForecaster:
    """Produces calibrated pre-flight cost forecasts.

    Args:
        budget_config: Live :class:`BudgetConfig`; supplies the static
            priors, the prior pseudo-count, and the currency stamp.
        history_lookup: Optional callable
            ``(tier, role_id) -> Sequence[float]`` returning historical
            cost-per-turn observations for a role on a tier. Defaults
            to "no history": cold-start blend collapses to the static
            prior. The work-pipeline wiring overrides this with a
            CostTracker-backed lookup.
        clock: Optional clock seam returning UTC ``datetime`` for
            forecast timestamps; defaults to ``datetime.now(UTC)``
            for production use. Tests inject a ``FakeClock``.

    Note:
        The forecaster is stateless apart from the injected
        dependencies; it is safe to call from multiple coroutines
        concurrently.
    """

    def __init__(
        self,
        *,
        budget_config: BudgetConfig,
        history_lookup: HistoryLookup | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        self._config = budget_config
        self._history_lookup: HistoryLookup = (
            history_lookup if history_lookup is not None else _empty_history
        )
        self._clock: ClockFn = clock if clock is not None else utc_now

    async def forecast(self, signal: BriefSignal) -> Forecast:
        """Produce a forecast for ``signal``.

        Returns a ``pending`` :class:`Forecast` with the bayesian-blended
        point estimate, the uncertainty band, and the canonical brief
        hash. The caller persists the row via
        :class:`CostForecastRepository` and surfaces the row for
        operator decision.

        Args:
            signal: The brief shape + role skeleton + model assignment.

        Returns:
            A fresh ``pending`` forecast row.

        Raises:
            ValueError: When ``signal.role_skeleton`` is empty
                (cannot estimate without roles).
        """
        if not signal.role_skeleton:
            logger.warning(
                BUDGET_PREFLIGHT_ERROR,
                reason="empty_role_skeleton",
                currency=signal.currency,
            )
            msg = "Cannot forecast: role_skeleton is empty"
            raise ValueError(msg)

        point_estimate, lower, upper, cold_start = await self._estimate_band(signal)
        now = self._clock()

        forecast_id: UUID = uuid4()
        brief_hash = compute_brief_hash(signal)
        forecast = Forecast(
            forecast_id=forecast_id,
            brief_hash=brief_hash,
            estimated_cost=point_estimate,
            lower_bound=lower,
            upper_bound=upper,
            currency=signal.currency,
            decision=ForecastDecision.PENDING,
            decided_at=None,
            decided_by=None,
            ceiling_amount=None,
            created_at=now,
            updated_at=now,
        )
        logger.info(
            BUDGET_FORECAST_GENERATED,
            forecast_id=str(forecast_id),
            brief_hash=brief_hash,
            estimated_cost=point_estimate,
            lower_bound=lower,
            upper_bound=upper,
            currency=signal.currency,
            roles=len(signal.role_skeleton),
            cold_start=cold_start,
        )
        return forecast

    async def _estimate_band(
        self,
        signal: BriefSignal,
    ) -> tuple[float, float, float, bool]:
        """Blend per-role priors with history into ``(point, lo, hi, cold)``.

        Each role's static per-turn prior is blended (Bayesian shrinkage)
        with whatever observations ``history_lookup`` returns for the
        ``(tier, role)`` key. The band is cold-start (a fixed fraction of
        the point estimate) when no role had history, else the
        sqrt-sum-of-squares of per-role stddevs floored at a coefficient
        of the point estimate so it never claims unrealistic precision.

        Returns:
            Tuple ``(float, float, float, bool)``.
        """
        turns_per_role = (
            signal.estimated_turns_per_role
            if signal.estimated_turns_per_role is not None
            else _DEFAULT_TURNS_PER_ROLE
        )
        # Canonicalise exactly as compute_brief_hash does so two briefs that
        # hash equal also estimate equal: roles are normalised and assignment
        # keys normalised / values stripped. A raw role or un-stripped model id
        # would miss the assignment, skew the tier, and pass a divergent key to
        # history_lookup, making same-hash forecasts disagree.
        roles = tuple(normalize_identifier(role) for role in signal.role_skeleton)
        normalized_assignments = {
            normalize_identifier(role): model_id.strip()
            for role, model_id in signal.model_assignments.items()
        }
        tiers: list[str] = []
        for role_id in roles:
            model_id = normalized_assignments.get(role_id, "")
            tier = tier_from_model_id(model_id) if model_id else "medium"
            tiers.append(tier if tier is not None else "medium")

        # Per-role history lookups are independent; fan them out so a
        # many-role brief does not pay the sum of their latencies.
        async def _lookup(tier: str, role_id: str) -> Sequence[float]:
            """Lookup.

            Returns:
                Result of type ``Sequence[float]``.
            """
            return await self._history_lookup(tier, role_id)

        async with asyncio.TaskGroup() as tg:
            lookup_tasks = [
                tg.create_task(_lookup(tier, role_id))
                for tier, role_id in zip(tiers, roles, strict=True)
            ]
        observations_by_role = [task.result() for task in lookup_tasks]

        per_role_estimates: list[float] = []
        per_role_stddevs: list[float] = []
        cold_start = True
        for tier, observations in zip(tiers, observations_by_role, strict=True):
            if observations:
                cold_start = False
            blended_per_turn, std_per_turn = _bayesian_blend(
                prior_mean=_static_prior_per_turn(self._config, tier),
                prior_weight=self._config.forecast_shrinkage_prior_weight,
                observations=observations,
            )
            per_role_estimates.append(blended_per_turn * turns_per_role)
            per_role_stddevs.append(std_per_turn * turns_per_role)

        point_estimate = sum(per_role_estimates)
        if cold_start:
            band = point_estimate * _COLD_START_BAND_FRACTION
        else:
            warm_std = math.sqrt(sum(s * s for s in per_role_stddevs))
            band = max(warm_std, point_estimate * _WARM_BAND_FLOOR_COEFFICIENT)
        lower = max(0.0, point_estimate - band)
        upper = point_estimate + band
        return point_estimate, lower, upper, cold_start


__all__ = [
    "BriefSignal",
    "ClockFn",
    "CostForecaster",
    "HistoryLookup",
    "compute_brief_hash",
]
