# module-kind: orchestrator
"""Golden-company benchmark runner entry point.

``run_benchmark`` boots a SynthOrg company per brief, runs the brief through a
direct ``AgentEngine`` with a DETERMINISTIC provider (no real LLM spend),
captures the process-fact events the scorer tracks, grades the deliverable, and
assembles a per-release :class:`~evals.models.scorecard.Scorecard` emitted as
JSON + Markdown.

Determinism is provided by a config-selectable provider seam: a ``ScriptedDriver``
(the in-repo default, free + reproducible) or a recorded cassette replayed via
``CassetteCompletionProvider``. The learning curve (#1983) re-runs this entry
point across rounds while a learning subsystem accumulates procedural memory:
the score rises because the company produces better deliverables, and goes flat
when learning is disabled. See ``tests/unit/evals/`` for the curve experiment.
"""

import asyncio
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final
from uuid import UUID

from evals.errors import CompanyConfigInvalidError
from evals.history import ScorecardHistory
from evals.loader.anchors import AnchorSet, load_anchor_set
from evals.loader.briefs import load_brief_suite
from evals.models.brief import Brief, BriefKind
from evals.models.scorecard import (
    AggregatedProcessFacts,
    BriefResult,
    JudgeCalibrationReport,
    Scorecard,
)
from evals.runner.execution import BriefRunOutcome, run_brief
from evals.runner.grading import grade_brief
from evals.runner.penalties import (
    BENCHMARK_PENALTY_TABLE,
    PENALTY_CLASS_BRIEF_BUDGET_OVER,
)
from evals.runner.strategies import CleanCompletionStrategy
from evals.scoring.aggregate import aggregate_brief_score
from evals.scoring.judged import JudgeProtocol, ScriptedJudge
from synthorg.config.loader import load_config
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_SUITE_RUN_COMPLETE,
    EVALS_SUITE_RUN_START,
)
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

# Stable agent id so procedural memory accumulates against one agent across the
# rounds of a learning-curve run (retrieval keys on agent_id).
_BENCHMARK_AGENT_ID: UUID = UUID("00000000-0000-4000-8000-0000000000e7")
_DEFAULT_PROVIDER_NAME: str = "benchmark-provider"
_DEFAULT_MODEL_ID: str = "benchmark-model-001"
# Midpoint per-dimension score the default deterministic judge assigns to a
# deliverable it has no specific response for; keeps a bare run scoreable.
_DEFAULT_DELIVERABLE_SCORE: Final[float] = 0.5
# Hex characters of the suite-version digest retained in the scorecard; long
# enough to be collision-free for a brief suite, short enough to read.
_SUITE_VERSION_DIGEST_LEN: Final[int] = 16


def _default_identity(provider_name: str) -> AgentIdentity:
    """Build the stable benchmark agent identity bound to *provider_name*.

    Returns:
        The benchmark agent identity (stable id across rounds).
    """
    return AgentIdentity(
        id=_BENCHMARK_AGENT_ID,
        name="Benchmark Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider=provider_name, model_id=_DEFAULT_MODEL_ID),
        hiring_date=date(2026, 1, 1),
    )


def _resolve_provider_name(provider: CompletionProvider | None) -> str:
    """Return the provider name to bind the agent identity to.

    Returns:
        The provider's name, or the default benchmark provider name.
    """
    name = getattr(provider, "provider_name", None)
    return name if isinstance(name, str) and name else _DEFAULT_PROVIDER_NAME


def _suite_version(briefs: tuple[Brief, ...]) -> str:
    """Derive a stable suite version from the sorted brief ids.

    Returns:
        A ``sha256:``-prefixed stable digest of the brief ids.
    """
    joined = "|".join(b.brief_id for b in briefs)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return "sha256:" + digest[:_SUITE_VERSION_DIGEST_LEN]


def _provider_descriptor(
    provider: CompletionProvider | None, cassette: Path | None
) -> str:
    """Human-readable descriptor of the determinism source for the scorecard.

    Returns:
        A ``cassette:`` or ``scripted:`` descriptor string.
    """
    if cassette is not None:
        return f"cassette:{cassette.name}"
    return f"scripted:{_resolve_provider_name(provider)}"


def _build_default_judge(anchor_sets: tuple[AnchorSet, ...]) -> ScriptedJudge:
    """Build a deterministic judge calibrated by echoing anchor hand-scores.

    The judge reproduces each anchor's hand-scores exactly (perfect ordinal
    calibration) and falls back to a neutral midpoint for any deliverable it has
    no specific response for. Experiments inject a richer judge that scores
    specific deliverable texts.

    Returns:
        A calibrated, deterministic ``ScriptedJudge``.
    """
    responses: dict[str, dict[str, float]] = {}
    dimensions: set[str] = set()
    for anchor_set in anchor_sets:
        for item in anchor_set.items:
            responses[item.output] = dict(item.hand_scores)
            dimensions.update(item.hand_scores)
    default_scores = dict.fromkeys(dimensions, _DEFAULT_DELIVERABLE_SCORE)
    return ScriptedJudge(responses=responses, default_scores=default_scores)


def _resolve_anchors_dir(brief_suite: Path, anchors_dir: Path | None) -> Path:
    """Resolve the anchors directory (defaults to a sibling ``anchors/`` dir).

    Returns:
        The resolved anchors directory path.
    """
    return anchors_dir if anchors_dir is not None else brief_suite.parent / "anchors"


def _judged_rubric_ids(briefs: tuple[Brief, ...]) -> tuple[str, ...]:
    """Distinct rubric ids across the judged briefs in the suite.

    Returns:
        A tuple of distinct judged-rubric ids in first-seen order.
    """
    seen: dict[str, None] = {}
    for brief in briefs:
        if brief.kind is BriefKind.JUDGED and brief.rubric is not None:
            seen[brief.rubric.rubric_id] = None
    return tuple(seen)


def _build_brief_result(
    brief: Brief,
    *,
    grade: int,
    termination_reason: str,
    tracked_events: dict[str, int],
    calibration: JudgeCalibrationReport | None,
) -> BriefResult:
    """Combine grade + process facts into a scorecard row for *brief*.

    Returns:
        The assembled :class:`BriefResult` row.
    """
    aggregation = aggregate_brief_score(grade, tracked_events, BENCHMARK_PENALTY_TABLE)
    from evals.models.scorecard import ProcessFactReport  # noqa: PLC0415

    report = ProcessFactReport(
        events_by_class=dict(tracked_events),
        entries=aggregation.entries,
    )
    return BriefResult(
        brief_id=brief.brief_id,
        kind=brief.kind,
        grade=aggregation.grade,
        deduction=aggregation.deduction,
        score=aggregation.score,
        score_floor=BENCHMARK_PENALTY_TABLE.floor,
        process_facts=report,
        termination_reason=NotBlankStr(termination_reason),
        judge_calibration=calibration,
    )


def _tracked_with_synthetic(
    outcome: BriefRunOutcome,
    *,
    run_hard_ceiling: float,
) -> dict[str, int]:
    """Augment captured events with runner-measured synthetic process facts.

    A run whose measured cost meets or exceeds the company's per-run hard
    ceiling (when one is configured) is attributed a synthetic budget-over
    process fact, mirroring the wall-clock breach the spine already models.

    Returns:
        The event-class counts including any synthetic penalty.
    """
    tracked = dict(outcome.tracked_events)
    if run_hard_ceiling > 0 and outcome.total_cost >= run_hard_ceiling:
        tracked[PENALTY_CLASS_BRIEF_BUDGET_OVER] = (
            tracked.get(PENALTY_CLASS_BRIEF_BUDGET_OVER, 0) + 1
        )
    return tracked


async def _score_briefs(  # noqa: PLR0913
    engine: AgentEngine,
    briefs: tuple[Brief, ...],
    *,
    identity: AgentIdentity,
    judge: JudgeProtocol,
    anchors_dir: Path,
    work_dir: Path,
    run_hard_ceiling: float,
) -> tuple[tuple[BriefResult, ...], dict[str, JudgeCalibrationReport]]:
    """Run + grade every brief, returning the rows and judge calibrations.

    Returns:
        ``(brief_results, calibrations_by_rubric_id)``.
    """
    results: list[BriefResult] = []
    calibrations: dict[str, JudgeCalibrationReport] = {}
    for brief in briefs:
        outcome = await run_brief(engine, brief, identity=identity)
        grade, calibration = grade_brief(
            brief,
            deliverable_text=outcome.deliverable_text,
            work_dir=work_dir,
            judge=judge,
            anchors_dir=anchors_dir,
        )
        if calibration is not None:
            calibrations[calibration.rubric_id] = calibration
        results.append(
            _build_brief_result(
                brief,
                grade=grade,
                termination_reason=outcome.termination_reason,
                tracked_events=_tracked_with_synthetic(
                    outcome, run_hard_ceiling=run_hard_ceiling
                ),
                calibration=calibration,
            )
        )
    return tuple(results), calibrations


def _aggregate_process_facts(
    results: tuple[BriefResult, ...],
) -> AggregatedProcessFacts:
    """Sum every brief's tracked process-fact counts into the suite rollup.

    Returns:
        The suite-level :class:`AggregatedProcessFacts` rollup.
    """
    rollup: dict[str, int] = {}
    for result in results:
        for event, count in result.process_facts.events_by_class.items():
            rollup[event] = rollup.get(event, 0) + count
    return AggregatedProcessFacts(
        total_events=sum(rollup.values()),
        events_by_class=rollup,
    )


async def run_benchmark_async(  # noqa: PLR0913
    *,
    company_config: Path,
    brief_suite: Path,
    out_dir: Path,
    anchors_dir: Path | None = None,
    cassette: Path | None = None,
    provider: CompletionProvider | None = None,
    judge: JudgeProtocol | None = None,
    memory_backend: MemoryBackend | None = None,
    procedural_config: ProceduralMemoryConfig | None = None,
    history_dir: Path | None = None,
) -> Scorecard:
    """Run the brief suite against the company config and return the scorecard.

    Args:
        company_config: Path to the company RootConfig YAML.
        brief_suite: Directory of brief YAML files.
        out_dir: Directory the scorecard JSON + Markdown are written to.
        anchors_dir: Anchor-set directory (defaults to ``<suite>/../anchors``).
        cassette: Optional recorded cassette (records the determinism source).
        provider: Deterministic completion provider (defaults to a
            ``ScriptedDriver``).
        judge: Calibrated judge for judged briefs (defaults to an anchor-echo
            ``ScriptedJudge``).
        memory_backend: Procedural-memory backend; enables the live
            capture/inject pipeline so accumulated memory moves the score.
        procedural_config: Procedural-memory capture config (enables failure
            capture when paired with a backend).
        history_dir: When set, the scorecard is also appended to this
            scorecard-history directory so the learning curve can be assembled
            across runs.

    Returns:
        The assembled, schema-versioned scorecard.

    Raises:
        CompanyConfigInvalidError: The company YAML failed validation.
    """
    try:
        root_config = load_config(company_config)
    except Exception as exc:
        msg = (
            f"company config {company_config} failed validation: "
            f"{safe_error_description(exc)}"
        )
        raise CompanyConfigInvalidError(msg) from exc

    briefs = load_brief_suite(brief_suite)
    resolved_anchors = _resolve_anchors_dir(brief_suite, anchors_dir)
    active_provider: CompletionProvider = provider or ScriptedDriver(
        _DEFAULT_PROVIDER_NAME, strategy=CleanCompletionStrategy()
    )
    identity = _default_identity(_resolve_provider_name(active_provider))

    active_judge: JudgeProtocol
    if judge is not None:
        active_judge = judge
    else:
        anchor_sets = tuple(
            load_anchor_set(resolved_anchors, rubric_id)
            for rubric_id in _judged_rubric_ids(briefs)
        )
        active_judge = _build_default_judge(anchor_sets)

    if memory_backend is not None and not memory_backend.is_connected:
        await memory_backend.connect()

    # The company config drives the learning subsystem: an injected config wins,
    # else the company's own ``memory.procedural`` section decides whether
    # failure capture runs. A company with procedural memory disabled produces a
    # measurably flatter learning curve (the #1983 control condition).
    effective_procedural = (
        procedural_config
        if procedural_config is not None
        else root_config.memory.procedural
    )
    # Wire context injection so the engine surfaces accumulated procedural
    # memory through its OWN dispatch (``_prepare_context`` -> ``prepare_messages``)
    # rather than the harness pre-retrieving it. The curve experiment therefore
    # proves the live capture -> store -> retrieve -> inject loop end to end.
    injection_strategy: MemoryInjectionStrategy | None = (
        ContextInjectionStrategy(
            backend=memory_backend,
            config=MemoryRetrievalConfig(),
        )
        if memory_backend is not None
        else None
    )
    engine = AgentEngine(
        provider=active_provider,
        recovery_strategy=FailAndReassignStrategy(),
        procedural_memory_config=effective_procedural,
        memory_injection_strategy=injection_strategy,
        memory_backend=memory_backend,
    )

    logger.info(
        EVALS_SUITE_RUN_START,
        company_name=root_config.company_name,
        company_config=str(company_config),
        brief_count=len(briefs),
        determinism_source=_provider_descriptor(active_provider, cassette),
    )

    brief_results, calibrations = await _score_briefs(
        engine,
        briefs,
        identity=identity,
        judge=active_judge,
        anchors_dir=resolved_anchors,
        work_dir=out_dir,
        run_hard_ceiling=root_config.budget.run_hard_ceiling,
    )
    scorecard = Scorecard(
        generated_at=datetime.now(UTC),
        company_config_path=NotBlankStr(str(company_config)),
        cassette_path=NotBlankStr(_provider_descriptor(active_provider, cassette)),
        cassette_sha256=NotBlankStr(
            hashlib.sha256(
                _provider_descriptor(active_provider, cassette).encode("utf-8")
            ).hexdigest()
        ),
        suite_version=NotBlankStr(_suite_version(briefs)),
        briefs=brief_results,
        process_facts=_aggregate_process_facts(brief_results),
        judge_calibrations=tuple(calibrations.values()),
    )

    _emit(scorecard, out_dir)
    if history_dir is not None:
        ScorecardHistory(history_dir).record(scorecard)
    logger.info(
        EVALS_SUITE_RUN_COMPLETE,
        total=scorecard.total,
        max_total=scorecard.max_total,
        is_passing=scorecard.is_passing,
    )
    return scorecard


def _emit(scorecard: Scorecard, out_dir: Path) -> None:
    """Write the scorecard JSON + Markdown into *out_dir*."""
    from evals.emit.json_writer import write_scorecard_json  # noqa: PLC0415
    from evals.emit.markdown_writer import write_scorecard_md  # noqa: PLC0415

    out_dir.mkdir(parents=True, exist_ok=True)
    write_scorecard_json(scorecard, out_dir)
    write_scorecard_md(scorecard, out_dir)


def run_benchmark(  # noqa: PLR0913
    *,
    company_config: Path,
    brief_suite: Path,
    cassette: Path | None = None,
    out_dir: Path,
    anchors_dir: Path | None = None,
    provider: CompletionProvider | None = None,
    judge: JudgeProtocol | None = None,
) -> Scorecard:
    """Synchronous wrapper around :func:`run_benchmark_async`.

    Matches the acceptance test's call shape; the learning-curve experiment
    calls :func:`run_benchmark_async` directly so it can drive rounds on its own
    event loop with a shared memory backend.

    Returns:
        The assembled scorecard.
    """
    return asyncio.run(
        run_benchmark_async(
            company_config=company_config,
            brief_suite=brief_suite,
            out_dir=out_dir,
            anchors_dir=anchors_dir,
            cassette=cassette,
            provider=provider,
            judge=judge,
        )
    )


__all__ = ["run_benchmark", "run_benchmark_async"]
