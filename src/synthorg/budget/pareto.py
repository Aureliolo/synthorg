"""Cost / quality Pareto frontier models.

The Pareto frontier is the operator-facing answer to "90 percent of the
quality at 40 percent of the cost if you downgrade these roles".

This module ships the value models (:class:`ParetoPoint`,
:class:`ParetoFrontier`). The analyzer itself
(:class:`ParetoAnalyzer`) lands in Phase 7 of #1982; the models go in
first so the persistence layer, controllers, and dashboard can be
authored against a stable shape.
"""

from datetime import datetime  # noqa: TC003 -- required at runtime by Pydantic

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class ParetoPoint(BaseModel):
    """A single downgrade candidate on the cost / quality frontier.

    Each point answers "if you downgrade ``role_id`` from
    ``current_model`` to ``candidate_model``, you lose
    ``quality_delta_pct`` of quality and save ``cost_saving_pct`` of
    cost". The :attr:`source` carries the provenance of the benchmark
    score used to compute the quality delta so the dashboard can flag
    stub data versus measured data.

    Attributes:
        role_id: Identifier of the role being analysed (typically the
            agent identity id rendered as a string).
        role_label: Human-readable role label for the dashboard
            (e.g. ``"Backend Engineer"``).
        current_model: Canonical model id currently assigned to the
            role.
        candidate_model: Canonical model id of the proposed downgrade.
        quality_delta_pct: Percent of quality lost, computed as
            ``current_score - candidate_score`` (0 to 100).
        cost_saving_pct: Percent of cost saved, computed as
            ``(current_cost_per_task - candidate_cost_per_task) /
            current_cost_per_task * 100`` (0 to 100).
        source: Provenance of the benchmark scores used (verbatim from
            :attr:`BenchmarkScore.source`).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role_id: NotBlankStr = Field(description="Identifier of the role")
    role_label: NotBlankStr = Field(description="Human-readable role label")
    current_model: NotBlankStr = Field(description="Canonical model id in use")
    candidate_model: NotBlankStr = Field(description="Canonical model id of downgrade")
    quality_delta_pct: float = Field(
        ge=0.0,
        le=100.0,
        description="Percent of quality lost (0 to 100)",
    )
    cost_saving_pct: float = Field(
        ge=0.0,
        le=100.0,
        description="Percent of cost saved (0 to 100)",
    )
    source: NotBlankStr = Field(description="Benchmark-score provenance identifier")


class ParetoFrontier(BaseModel):
    """The full cost / quality frontier for a company at a moment in time.

    Points are returned sorted by ``cost_saving_pct`` descending so the
    dashboard's "biggest wins first" rendering is a direct iteration.

    Attributes:
        points: Tuple of :class:`ParetoPoint`, sorted by
            ``cost_saving_pct`` descending.
        generated_at: When the analyzer produced this frontier.
        baseline_window_size: Number of historical task records the
            analyzer consulted (the
            ``budget.baseline_window_size`` setting at boot).
        source: Provenance summary string. Equal to the most
            permissive component source when multiple providers
            contributed (e.g. ``"stub:calibrated-v1"`` if any stub
            score was used).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    points: tuple[ParetoPoint, ...] = Field(
        default=(),
        description="Frontier points sorted by cost_saving_pct descending",
    )
    generated_at: datetime = Field(description="When the analyzer ran")
    baseline_window_size: int = Field(
        ge=1,
        description="Historical records consulted",
    )
    source: NotBlankStr = Field(description="Aggregate provenance identifier")
