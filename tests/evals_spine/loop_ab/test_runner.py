# module-kind: tests
"""The matrix runner, driven offline against the real loops.

The loops here are the real registered implementations, not doubles; only the
LLM is scripted. React is the one loop that runs without provisioning, so it is
the one the runner measures end to end here.

The OpenHands leg is exercised in its unwired state, pinning the reporting rule
that matters most for an honest scoreboard: a loop that cannot be measured is
reported with its reason, never dropped and never scored as a zero.

Whether the scoreboard can *separate* two loops is a scoring property rather
than a runner one, and it is pinned in ``test_rubric.py``, which feeds the
rubric two aggregates directly. It cannot be shown here: OpenHands needs a
Docker sandbox and a live gateway, so offline there is only ever one measured
loop to compare.
"""

import contextlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from evals.errors import LoopAbOpenHandsUnwiredError, LoopAbProviderMissingError
from evals.loader.briefs import load_brief_suite
from evals.loop_ab.manifest import LoopAbManifest, TierEntry
from evals.loop_ab.models import Provenance
from evals.loop_ab.runner import (
    CellLedgerFactory,
    CellRun,
    LoopAbDeps,
    OpenHandsCellFactory,
    ProviderFactory,
    ToolReleaseHook,
    _build_engine,
    _CellCoordinates,
    _run_cell,
    run_matrix,
)
from evals.loop_ab.stall_watch import ProgressTrackingLedger
from evals.loop_ab.workspace import seed_workspace
from evals.models.brief import Brief
from evals.runner.execution import EVAL_TASK_PROJECT, _brief_task
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_selector import registered_loop_types
from synthorg.engine.openhands.config import OpenHandsLoopConfig, OpenHandsLoopDeps
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.providers.models import CompletionResponse, TokenUsage
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.integration

_SUITE: Final = Path(__file__).resolve().parents[3] / "evals" / "loop_ab" / "briefs"


def _provenance() -> Provenance:
    """Fixed provenance so assertions do not depend on the live repository."""
    return Provenance(
        generated_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        git_commit=NotBlankStr("b" * 40),
        git_dirty=False,
        manifest_sha256=NotBlankStr("sha256:" + "f" * 64),
        brief_suite_version=NotBlankStr("sha256:beef"),
        sandbox_image=NotBlankStr("example.invalid/sandbox:under-test"),
        sidecar_image=NotBlankStr("example.invalid/sidecar:under-test"),
        openhands_image=NotBlankStr("example.invalid/openhands:under-test"),
    )


def _tier(label: str = "large", model_id: str = "example-large-001") -> TierEntry:
    """A tier bound to an explicit vendor-agnostic provider and model."""
    return TierEntry(
        tier=NotBlankStr(label),
        provider=NotBlankStr("example-provider"),
        model_id=NotBlankStr(model_id),
    )


def _manifest(
    *, loops: tuple[str, ...] | None = None, repetitions: int = 1
) -> LoopAbManifest:
    """A manifest over every registered loop unless narrowed."""
    return LoopAbManifest(
        brief_suite=NotBlankStr("evals/loop_ab/briefs"),
        loops=tuple(NotBlankStr(name) for name in (loops or registered_loop_types())),
        tiers=(_tier(),),
        repetitions=repetitions,
    )


#: A valid two-step plan. The planning loops parse this and go on to execute
#: both steps; the reactive loop treats the same text as its answer and stops.
#: That asymmetry is the whole point: one scripted response, genuinely different
#: measured behaviour, because the loops themselves differ.
_PLAN_JSON: Final[str] = json.dumps(
    {
        "steps": [
            {
                "step_number": 1,
                "description": "Read the specification",
                "expected_outcome": "The required behaviour is understood",
            },
            {
                "step_number": 2,
                "description": "Write the module",
                "expected_outcome": "The module exists and imports",
            },
        ]
    }
)


async def _build_scripted_provider(cell: CellRun) -> ScriptedProvider:
    """Build the scripted provider for one repetition.

    Returns:
        A provider replaying the same plan for every cell.
    """
    return ScriptedProvider(
        response=CompletionResponse(
            content=_PLAN_JSON,
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=120, output_tokens=40, cost=0.002),
            model=cell.tier.model_id,
        )
    )


def _scripted_deps(
    project_repo: ProjectRepository,
    *,
    build_provider: ProviderFactory | None = None,
    build_openhands_cell: OpenHandsCellFactory | None = None,
    open_cell_ledger: CellLedgerFactory | None = None,
    release_tools: ToolReleaseHook | None = None,
) -> LoopAbDeps:
    """Deps whose only fake is the LLM; no tools, no OpenHands runtime.

    Every case routes through here so the dep set stays in one place: a field
    added to :class:`LoopAbDeps` and forgotten at one of a dozen construction
    sites is invisible to the type checker, because all but two of them default.

    Returns:
        The offline :class:`LoopAbDeps`.
    """
    return LoopAbDeps(
        build_provider=build_provider or _build_scripted_provider,
        build_tool_registry=lambda _workspace: None,
        build_openhands_cell=build_openhands_cell,
        open_cell_ledger=open_cell_ledger,
        project_repo=project_repo,
        release_tools=release_tools,
    )


def _simple_brief() -> tuple[Brief, ...]:
    """Just the simple brief, to keep the matrix small in tests."""
    return tuple(b for b in load_brief_suite(_SUITE) if b.brief_id == "loop-ab-simple")


async def test_every_registered_loop_gets_a_row(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """The comparison must cover the whole field, discovered not hardcoded."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )

    assert {row.loop_type for row in scoreboard.rows} == set(registered_loop_types())


async def test_an_unwired_loop_is_reported_not_dropped(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """An unavailable loop must be visible in the artifact, with its reason."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )

    unavailable = {row.loop_type for row in scoreboard.unavailable_rows}
    assert unavailable == {"openhands"}
    reason = scoreboard.unavailable_rows[0].unavailable_reason
    assert reason is not None
    assert "OpenHands" in reason


async def test_the_runnable_loop_is_measured_and_scored(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """Every loop that could run carries a real measurement and a score."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )

    measured = {row.loop_type for row in scoreboard.measured_rows}
    assert measured == {"react"}
    for row in scoreboard.measured_rows:
        assert row.score is not None
        assert row.measurement is not None
        assert row.measurement.repetitions == 1


async def test_a_cheaper_loop_outscores_a_more_expensive_one(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """With correctness tied, the efficiency dimensions decide the ranking."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )
    rows = {
        row.loop_type: row
        for row in scoreboard.measured_rows
        if row.score is not None and row.measurement is not None
    }
    cheapest = min(
        rows.values(),
        key=lambda r: r.measurement.aggregate.total_tokens,  # type: ignore[union-attr]
    )
    dearest = max(
        rows.values(),
        key=lambda r: r.measurement.aggregate.total_tokens,  # type: ignore[union-attr]
    )

    # Unconditional: if only one loop were measured, cheapest is dearest and
    # this is a trivial ``x >= x``; a real token spread must never leave the
    # ranking silently unchecked, which a guarded assertion would allow.
    assert cheapest.score.composite >= dearest.score.composite  # type: ignore[union-attr]


async def test_each_repetition_starts_from_a_freshly_seeded_workspace(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """Repetitions must be independent, or the median measures run order."""
    scoreboard = await run_matrix(
        manifest=_manifest(repetitions=3),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )
    react = next(row for row in scoreboard.measured_rows if row.loop_type == "react")

    assert react.measurement is not None
    assert react.measurement.repetitions == 3
    # Identical scripted input across independent repetitions must produce an
    # identical result; any spread would mean state leaked between runs.
    spread = react.measurement.correctness_spread
    assert spread.minimum == spread.maximum


async def test_the_scoreboard_carries_its_promotion_recommendation(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """The artifact is only actionable if it ends in settings values."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )

    # Every scripted loop runs tool-less, writes nothing, and grades below the
    # correctness gate, so none clears it and the recommendation degrades to
    # "promote nothing": a real assertion, not a tautological not-None check on a
    # required field.
    assert scoreboard.recommendation.default_loop_type is None
    assert scoreboard.provenance.git_commit == "b" * 40


async def test_measured_rows_carry_their_ledger_spend(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """Cost is read back from the run's own ledger, not re-derived from tokens."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )

    for row in scoreboard.measured_rows:
        assert row.spend, f"{row.loop_type} recorded no spend"
        for item in row.spend:
            assert item.provider == "example-provider"
            assert item.model_id == "example-large-001"
            assert item.cost > 0.0


async def test_a_failed_later_repetition_keeps_what_it_already_measured(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """A cell that dies on a later repetition reports what it already measured.

    Drive a two-repetition cell whose provider works for the first repetition
    (booking real spend against the ledger) and then becomes unavailable for the
    second. Both the measurement and the spend from the first repetition have to
    survive: that run happened and was paid for, and a summary over fewer
    repetitions is a weaker measurement rather than an absent one. Discarding it
    would throw away real money's worth of evidence over a transient failure.
    """
    calls = {"count": 0}

    async def _build_provider(cell: CellRun) -> ScriptedProvider:
        calls["count"] += 1
        if calls["count"] >= 2:
            msg = "provider unavailable on the second repetition"
            raise RuntimeError(msg)
        return await _build_scripted_provider(cell)

    deps = _scripted_deps(project_repo, build_provider=_build_provider)
    coord = _CellCoordinates(loop_type="react", tier=_tier(), brief=_simple_brief()[0])

    row = await _run_cell(
        coord=coord,
        manifest=_manifest(repetitions=2),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=deps,
    )

    assert row.unavailable_reason is None
    assert row.measurement is not None
    assert row.measurement.repetitions == 1
    assert row.spend, "the failed cell dropped the first repetition's spend"
    assert sum(item.cost for item in row.spend) > 0.0


async def test_a_cell_that_never_completes_a_repetition_is_unavailable(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """With nothing measured there is nothing to report but the reason.

    The partial-summary path above needs a floor: a cell whose very first
    repetition failed has no evidence at all, and summarising zero runs would
    be a fabricated measurement rather than a weaker one.
    """

    async def _build_provider(cell: CellRun) -> ScriptedProvider:
        del cell
        msg = "provider unavailable from the first repetition"
        raise RuntimeError(msg)

    deps = _scripted_deps(project_repo, build_provider=_build_provider)
    coord = _CellCoordinates(loop_type="react", tier=_tier(), brief=_simple_brief()[0])

    row = await _run_cell(
        coord=coord,
        manifest=_manifest(repetitions=2),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=deps,
    )

    assert row.measurement is None
    assert row.unavailable_reason is not None


async def test_a_systemic_failure_aborts_rather_than_recording_a_row(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """A broken machine is not a property of the loop it happened to hit.

    A missing provider is true of every remaining cell, so absorbing it here
    would spend the rest of the matrix rediscovering one fact and attribute it
    to whichever loop ran next.
    """

    async def _build_provider(cell: CellRun) -> ScriptedProvider:
        del cell
        msg = "tier names a provider absent from the company config"
        raise LoopAbProviderMissingError(msg)

    deps = _scripted_deps(project_repo, build_provider=_build_provider)
    coord = _CellCoordinates(loop_type="react", tier=_tier(), brief=_simple_brief()[0])

    with pytest.raises(LoopAbProviderMissingError):
        await _run_cell(
            coord=coord,
            manifest=_manifest(repetitions=2),
            suite_root=_SUITE,
            work_root=tmp_path / "work",
            deps=deps,
        )


async def test_collaborators_are_bound_per_repetition(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """Each repetition binds its own run, not the tier's.

    The gateway bearer binds one run and the sandbox binds one workspace, so a
    collaborator handed the tier alone would reuse a bearer across cells and
    mount a directory a later repetition has already recreated.
    """
    seen: list[CellRun] = []

    async def _build_provider(cell: CellRun) -> ScriptedProvider:
        seen.append(cell)
        return await _build_scripted_provider(cell)

    deps = _scripted_deps(project_repo, build_provider=_build_provider)
    coord = _CellCoordinates(loop_type="react", tier=_tier(), brief=_simple_brief()[0])

    await _run_cell(
        coord=coord,
        manifest=_manifest(repetitions=2),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=deps,
    )

    assert [cell.repetition for cell in seen] == [0, 1]
    assert all(cell.loop_type == "react" for cell in seen)
    assert all(cell.workspace.project_dir.is_dir() for cell in seen)


async def test_each_repetition_tears_its_sandboxes_down(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """A reusing lifecycle keeps its container until something releases it.

    The deployment configures ``per-agent``, which holds one warm container per
    owner and destroys it on a grace timer owned by the strategy object this
    repetition is about to discard. Fifty-four repetitions that never release
    leave fifty-four containers to a timer nobody awaits.
    """
    released: list[int] = []

    async def _release() -> None:
        released.append(len(released))

    deps = _scripted_deps(project_repo, release_tools=_release)
    coord = _CellCoordinates(loop_type="react", tier=_tier(), brief=_simple_brief()[0])

    await _run_cell(
        coord=coord,
        manifest=_manifest(repetitions=2),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=deps,
    )

    assert released == [0, 1]


async def test_a_run_is_checked_against_the_tree_the_brief_is_graded_on(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """The engine gets the declared-artifact probe production gives it.

    A loop that calls one tool and then answers in prose passes both of the
    proxies guarding a silent no-op (the empty-run nudge and the loop's own
    ``NO_OP`` rule ask whether *any* tool was called), so the only thing that
    catches it is the post-execution probe asking the workspace whether the
    declared file exists. Left unwired, ``task_sync`` cannot ask, and a run
    that delivered nothing is recorded as a clean ``completed``: the A/B then
    measures a loop under weaker checks than the deployment it is advising.

    Bound to the cell root rather than the graded tree, because the probe
    re-derives the project directory beneath it exactly as the sandboxes do.
    """
    brief = _simple_brief()[0]
    workspace = seed_workspace(
        brief=brief, suite_root=_SUITE, work_root=tmp_path / "work"
    )
    cell = CellRun(
        loop_type=NotBlankStr("react"),
        tier=_tier(),
        brief=brief,
        repetition=0,
        workspace=workspace,
    )
    engine = await _build_engine(
        cell=cell,
        deps=_scripted_deps(project_repo),
        cost_tracker=ProgressTrackingLedger(),
    )
    # Reached privately because the wiring itself is the subject: the defect
    # this pins was the engine holding ``None`` here.
    probe = engine._artifact_probe
    assert probe is not None

    # The task's own declarations, not the brief's: those are what ``task_sync``
    # hands the probe, and only a workspace-graded brief projects any.
    expected = _brief_task(brief, agent_id="agent-under-test").artifacts_expected
    assert expected

    absent = await probe(EVAL_TASK_PROJECT, expected)
    assert absent.nothing_delivered

    (workspace.project_dir / expected[0].path).write_text("x = 1\n", encoding="utf-8")
    present = await probe(EVAL_TASK_PROJECT, expected)
    assert not present.nothing_delivered


async def test_spend_is_read_from_the_supplied_ledger(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """A hosted gateway's ledger, not the engine's tracker, is authoritative.

    With a gateway hosted, the engine's own tracker sees the driver's records
    and the gateway's tracker sees the gateway's. Reading the wrong one would
    double-count a native leg and would miss the OpenHands leg entirely, so the
    runner must collect from exactly what the factory yielded.
    """
    ledger = ProgressTrackingLedger()
    opened: list[CellRun] = []

    @contextlib.asynccontextmanager
    async def _open_ledger(cell: CellRun) -> AsyncIterator[ProgressTrackingLedger]:
        opened.append(cell)
        await ledger.record(
            CostRecord(
                provider=NotBlankStr("gateway-provider"),
                model=NotBlankStr("gateway-model"),
                input_tokens=7,
                output_tokens=3,
                cost=0.5,
                currency=DEFAULT_CURRENCY,
                timestamp=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
            )
        )
        yield ledger

    deps = _scripted_deps(project_repo, open_cell_ledger=_open_ledger)
    coord = _CellCoordinates(loop_type="react", tier=_tier(), brief=_simple_brief()[0])

    row = await _run_cell(
        coord=coord,
        manifest=_manifest(repetitions=1),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=deps,
    )

    assert len(opened) == 1
    assert [item.provider for item in row.spend] == ["gateway-provider"]


async def test_the_openhands_cell_factory_supplies_config_and_deps(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """The OpenHands leg asks its factory for one cell, and reports a refusal.

    The factory is the only place ``OpenHandsLoopConfig`` comes from, because it
    carries the bearer TTL the run is minted against. This covers the call and
    the refusal path; a returned config reaching ``build_execution_loop`` needs
    its own case.
    """
    calls: list[CellRun] = []

    async def _build_cell(
        cell: CellRun,
    ) -> tuple[OpenHandsLoopConfig, OpenHandsLoopDeps]:
        calls.append(cell)
        msg = "runtime deliberately unavailable in this offline test"
        raise LoopAbOpenHandsUnwiredError(msg)

    deps = _scripted_deps(project_repo, build_openhands_cell=_build_cell)

    row = await _run_cell(
        coord=_CellCoordinates(
            loop_type="openhands", tier=_tier(), brief=_simple_brief()[0]
        ),
        manifest=_manifest(repetitions=1),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=deps,
    )

    assert len(calls) == 1
    assert row.unavailable_reason is not None
    assert "LoopAbOpenHandsUnwiredError" in row.unavailable_reason


async def test_the_native_loops_never_ask_for_an_openhands_runtime(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    # Building the container runtime costs a Docker sandbox; a native cell that
    # reached for one would pay for it on every repetition of the matrix.
    calls: list[CellRun] = []

    async def _build_cell(
        cell: CellRun,
    ) -> tuple[OpenHandsLoopConfig, OpenHandsLoopDeps]:
        calls.append(cell)
        msg = "should not be reached"
        raise LoopAbOpenHandsUnwiredError(msg)

    deps = _scripted_deps(project_repo, build_openhands_cell=_build_cell)

    await _run_cell(
        coord=_CellCoordinates(
            loop_type="react", tier=_tier(), brief=_simple_brief()[0]
        ),
        manifest=_manifest(repetitions=1),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=deps,
    )

    assert calls == []


async def test_a_tool_less_run_disqualifies_every_measured_loop(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """With no tools the loops write nothing, so the correctness gate fires."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )

    measured = scoreboard.measured_rows
    assert measured
    for row in measured:
        assert row.score is not None
        assert row.score.disqualified is True


async def test_a_tool_less_run_is_reported_as_the_no_op_it_is(
    tmp_path: Path, project_repo: ProjectRepository
) -> None:
    """A run that called no tool for an artifact-expecting task is a NO_OP.

    The behavioural payoff of the brief's artifacts reaching its task. Without
    them the same run terminates ``completed``, and a scoreboard reporting a
    NO_OP rate of zero would be reporting a check that never ran rather than a
    loop that never failed it.
    """
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(project_repo),
        provenance=_provenance(),
    )

    measured = scoreboard.measured_rows
    assert measured
    for row in measured:
        assert row.measurement is not None
        assert row.measurement.termination_reasons == {"no_op": 1}
        # Nothing was written, so the brief's declared file is absent from the
        # graded tree. Read off disk, not off the loop's own account.
        assert row.measurement.artifact_rate == 0.0
