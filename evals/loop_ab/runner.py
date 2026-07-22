# module-kind: orchestrator
"""Drive every registered loop over every brief and assemble the scoreboard.

One cell is a ``(loop, tier, brief, repetition)``. Each cell gets a workspace
recreated from the brief's seed fixture, an engine built around that one loop,
and a fresh cost tracker so the run's spend is attributable to it alone. The
loop then does the work with its own tools, and the brief's checks grade
whatever it actually left on disk.

Dependencies arrive through :class:`LoopAbDeps` rather than being constructed
here, so the same orchestration runs against a real provider at record time and
against scripted doubles in tests. That is also what keeps the OpenHands leg
honest: when its runtime is not wired, the cell is recorded as unavailable with
the reason instead of being dropped from the comparison.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

from evals.loop_ab.aggregate import (
    LoopRepetitionSummary,
    RepetitionOutcome,
    summarise_repetitions,
)
from evals.loop_ab.manifest import LoopAbManifest, TierEntry
from evals.loop_ab.models import (
    LoopBriefRow,
    Provenance,
    ProviderSpend,
    RubricWeights,
    Scoreboard,
)
from evals.loop_ab.promotion import recommend_promotion
from evals.loop_ab.rollup import rollup_by_complexity
from evals.loop_ab.rubric import LoopCellScore, score_cell
from evals.loop_ab.workspace import seed_workspace
from evals.models.brief import Brief
from evals.runner.execution import run_brief
from evals.runner.interpreter import resolve_checks
from evals.scoring.executable import grade_executable
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.budget.tracker_protocol import collect_all_records
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_selector import build_execution_loop
from synthorg.engine.openhands.config import OpenHandsLoopDeps
from synthorg.engine.openhands.errors import OpenHandsUnavailableError
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_LOOP_AB_LOOP_UNAVAILABLE,
    EVALS_LOOP_AB_RUN_RECORDED,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)

#: Stable agent id for every A/B run. Fixed so runs are comparable and so no
#: run inherits another's per-agent state.
_AB_AGENT_ID: UUID = UUID("00000000-0000-4000-8000-00000000ab00")

#: The loop's execution is the unit under test, so the agent is a plain
#: developer with no role-specific prompt shaping to advantage one loop.
_AB_AGENT_ROLE: str = "Developer"
_AB_AGENT_DEPARTMENT: str = "Engineering"
_AB_HIRING_DATE: date = date(2026, 1, 1)

ProviderFactory = Callable[[TierEntry], CompletionProvider]
ToolRegistryFactory = Callable[[Path], ToolRegistry | None]


@dataclass(frozen=True)
class LoopAbDeps:
    """Runtime collaborators the matrix is driven with.

    Attributes:
        build_provider: Builds the completion provider for a tier. At record
            time this returns a driver pointed at the LLM gateway's
            ``base_url``, so every loop's spend lands in the same authoritative
            ledger rather than being re-derived per loop.
        build_tool_registry: Builds the tool registry scoped to a run's
            workspace, giving the native loops their file and shell tools.
        openhands_loop_deps: Runtime deps for the OpenHands loop. ``None``
            records that loop as unavailable rather than skipping it.
    """

    build_provider: ProviderFactory
    build_tool_registry: ToolRegistryFactory
    openhands_loop_deps: OpenHandsLoopDeps | None = None


def _identity(tier: TierEntry) -> AgentIdentity:
    """Build the A/B agent bound to *tier*'s explicit provider and model.

    Returns:
        The agent identity for this tier.
    """
    return AgentIdentity(
        id=_AB_AGENT_ID,
        name="Loop A/B Agent",
        role=_AB_AGENT_ROLE,
        department=_AB_AGENT_DEPARTMENT,
        model=ModelConfig(provider=tier.provider, model_id=tier.model_id),
        hiring_date=_AB_HIRING_DATE,
    )


def _spend_from_records(records: tuple[CostRecord, ...]) -> tuple[ProviderSpend, ...]:
    """Fold a run's cost records into per-``(provider, model)`` spend.

    Returns:
        One :class:`ProviderSpend` per distinct provider and model.
    """
    totals: dict[tuple[str, str, str], tuple[int, int, float]] = {}
    for record in records:
        key = (record.provider, record.model, str(record.currency))
        seen_in, seen_out, seen_cost = totals.get(key, (0, 0, 0.0))
        totals[key] = (
            seen_in + record.input_tokens,
            seen_out + record.output_tokens,
            seen_cost + record.cost,
        )
    return tuple(
        ProviderSpend(
            provider=NotBlankStr(provider),
            model_id=NotBlankStr(model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            currency=NotBlankStr(currency),
        )
        for (provider, model, currency), (
            input_tokens,
            output_tokens,
            cost,
        ) in sorted(totals.items())
    )


def _build_engine(
    *,
    loop_type: str,
    tier: TierEntry,
    work_dir: Path,
    deps: LoopAbDeps,
    cost_tracker: CostTracker,
) -> AgentEngine:
    """Build an engine running exactly *loop_type* against *work_dir*.

    Returns:
        The configured :class:`AgentEngine`.

    Raises:
        OpenHandsUnavailableError: ``loop_type`` is openhands and its runtime
            deps are not wired.
    """
    execution_loop = build_execution_loop(
        loop_type, openhands_loop_deps=deps.openhands_loop_deps
    )
    return AgentEngine(
        provider=deps.build_provider(tier),
        execution_loop=execution_loop,
        tool_registry=deps.build_tool_registry(work_dir),
        cost_tracker=cost_tracker,
        recovery_strategy=FailAndReassignStrategy(),
    )


async def _run_repetition(  # noqa: PLR0913 -- orthogonal per-cell coordinates
    *,
    loop_type: str,
    tier: TierEntry,
    brief: Brief,
    suite_root: Path,
    work_root: Path,
    deps: LoopAbDeps,
) -> tuple[RepetitionOutcome, tuple[ProviderSpend, ...]]:
    """Run one loop once over one brief and grade what it produced.

    Returns:
        ``(outcome, spend)`` for this repetition.
    """
    work_dir = seed_workspace(brief=brief, suite_root=suite_root, work_root=work_root)
    # One tracker per run: ``run_brief`` derives a deterministic task id from the
    # brief alone, so records would otherwise pool across every loop and tier
    # measuring that brief and become unattributable.
    cost_tracker = CostTracker()
    engine = _build_engine(
        loop_type=loop_type,
        tier=tier,
        work_dir=work_dir,
        deps=deps,
        cost_tracker=cost_tracker,
    )

    outcome = await run_brief(engine, brief, identity=_identity(tier))
    graded = brief.model_copy(update={"checks": resolve_checks(brief.checks)})
    grade = grade_executable(graded, work_dir)
    metrics = outcome.metrics
    spend = _spend_from_records(await collect_all_records(cost_tracker))

    logger.info(
        EVALS_LOOP_AB_RUN_RECORDED,
        loop_type=loop_type,
        tier=tier.tier,
        brief_id=brief.brief_id,
        grade=grade.score,
        total_tokens=metrics.total_tokens,
        duration_seconds=metrics.duration_seconds,
        turns=metrics.total_turns,
        termination_reason=outcome.termination_reason,
    )
    return (
        RepetitionOutcome(
            correctness=grade.score,
            passed=grade.is_clean,
            termination_reason=outcome.termination_reason,
            metrics=metrics,
        ),
        spend,
    )


async def _run_cell(  # noqa: PLR0913 -- orthogonal per-cell coordinates
    *,
    loop_type: str,
    tier: TierEntry,
    brief: Brief,
    manifest: LoopAbManifest,
    suite_root: Path,
    work_root: Path,
    deps: LoopAbDeps,
) -> LoopBriefRow:
    """Run every repetition for one ``(loop, tier, brief)`` and build its row.

    A loop whose runtime is unavailable yields an unavailable row carrying the
    reason, never a missing row and never a fabricated zero.

    Returns:
        The assembled :class:`LoopBriefRow`.
    """
    outcomes: list[RepetitionOutcome] = []
    spend: list[ProviderSpend] = []
    for _ in range(manifest.repetitions):
        try:
            outcome, run_spend = await _run_repetition(
                loop_type=loop_type,
                tier=tier,
                brief=brief,
                suite_root=suite_root,
                work_root=work_root,
                deps=deps,
            )
        except OpenHandsUnavailableError as exc:
            logger.warning(
                EVALS_LOOP_AB_LOOP_UNAVAILABLE,
                loop_type=loop_type,
                tier=tier.tier,
                brief_id=brief.brief_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return LoopBriefRow(
                loop_type=NotBlankStr(loop_type),
                brief_id=brief.brief_id,
                tier=tier.tier,
                model_id=tier.model_id,
                unavailable_reason=(
                    f"{type(exc).__name__}: {safe_error_description(exc)}"
                ),
            )
        outcomes.append(outcome)
        spend.extend(run_spend)

    summary: LoopRepetitionSummary = summarise_repetitions(
        loop_type=loop_type, outcomes=tuple(outcomes)
    )
    return LoopBriefRow(
        loop_type=NotBlankStr(loop_type),
        brief_id=brief.brief_id,
        tier=tier.tier,
        model_id=tier.model_id,
        measurement=summary,
        score=None,
        spend=tuple(spend),
    )


def _score_rows(rows: tuple[LoopBriefRow, ...]) -> tuple[LoopBriefRow, ...]:
    """Score each ``(brief, tier)`` cell and attach the scores to their rows.

    Scoring is per cell because the efficiency dimensions are relative: a loop's
    token or latency score only means something against the other loops that
    ran the same brief on the same model.

    Returns:
        The rows with scores attached where a measurement exists.
    """
    cells: dict[tuple[str, str], list[LoopBriefRow]] = {}
    for row in rows:
        if row.measurement is not None:
            cells.setdefault((row.brief_id, row.tier), []).append(row)

    scored_by_row: dict[int, LoopCellScore] = {}
    for members in cells.values():
        scores = score_cell(tuple(row.measurement.aggregate for row in members))  # type: ignore[union-attr]
        for row, score in zip(members, scores, strict=True):
            scored_by_row[id(row)] = score

    return tuple(
        row.model_copy(update={"score": scored_by_row[id(row)]})
        if id(row) in scored_by_row
        else row
        for row in rows
    )


async def run_matrix(  # noqa: PLR0913 -- orthogonal matrix inputs
    *,
    manifest: LoopAbManifest,
    briefs: tuple[Brief, ...],
    suite_root: Path,
    work_root: Path,
    deps: LoopAbDeps,
    provenance: Provenance,
) -> Scoreboard:
    """Run the whole matrix and assemble the scoreboard.

    Args:
        manifest: The recording matrix.
        briefs: The loaded brief suite.
        suite_root: Directory brief seed fixtures resolve against.
        work_root: Directory per-run workspaces are created under.
        deps: Runtime collaborators.
        provenance: What this recording is measured against.

    Returns:
        The assembled :class:`Scoreboard`, including its promotion
        recommendation.
    """
    rows = [
        await _run_cell(
            loop_type=loop_type,
            tier=tier,
            brief=brief,
            manifest=manifest,
            suite_root=suite_root,
            work_root=work_root / tier.tier / loop_type,
            deps=deps,
        )
        for tier in manifest.tiers
        for brief in briefs
        for loop_type in manifest.loops
    ]

    scored = _score_rows(tuple(rows))
    estimates = {brief.brief_id: brief.estimated_complexity for brief in briefs}
    cells: dict[tuple[str, str], list[LoopCellScore]] = {}
    for row in scored:
        if row.score is not None:
            cells.setdefault((row.brief_id, row.tier), []).append(row.score)
    buckets = rollup_by_complexity(
        tuple(
            (estimates[brief_id], tuple(scores))
            for (brief_id, _tier), scores in sorted(cells.items())
        )
    )
    return Scoreboard(
        provenance=provenance,
        weights=RubricWeights.current(),
        rows=scored,
        recommendation=recommend_promotion(buckets),
    )


__all__ = ["LoopAbDeps", "ProviderFactory", "ToolRegistryFactory", "run_matrix"]
