"""Record the recursion-depth sweep against real providers.

The entry point behind ``make recursion-depth`` and ``make recursion-depth-record``.
It sweeps the decomposition depth cap with the merge gate on and with it off,
and writes the chart the question is answered from: what fraction of leaf work
survives to a correct merged result, against the depth a tree actually reached.

Two modes:

* **plan** (default): prints the matrix, the session ceiling and what a run
  would cost in sessions, and spends nothing. This is how to see the size of
  the bill before committing to it.
* **record** (``--record``): the real measurement, against real providers, with
  real spend.

There is deliberately no offline mode that regenerates the artifact. Only a real
run produces a curve, so a published number is always something that actually
happened; the harness is regression-tested offline by
``tests/evals_spine/recursion_depth``, which needs no spend.

A sweep executes agent-authored code, and never on the machine running it. The
agents run in the sandbox image the CLI verified, and so does everything that
grades what they produced: each unit's own suite, and the held-out oracle.
Grading a tree means importing whatever the agent wrote into it, so the process
that grades is a process the agent authored; on the host that would have had the
network, this recorder's own credentials and the Docker socket. See
``evals/recursion_depth/grading.py``.

Every session dispatches through the LLM gateway, and the recorder hosts that
gateway itself: the gateway verifies only bearers its own in-memory signer
minted, so owning the process that holds the signer is what makes the sweep
recordable at all, and it puts every unit on one authoritative cost ledger.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
from functools import partial
from pathlib import Path
from typing import Final

from evals.errors import (
    RecursionDepthCapabilityUnresolvedError,
    RecursionDepthJudgeNotIndependentError,
    RecursionDepthNoCellsMeasuredError,
    RecursionDepthSpendRepairEmptyError,
)
from evals.harness.binding import HarnessBinder
from evals.harness.host import (
    DEFAULT_CONTAINER_HOST,
    RecordingGatewayHost,
    RecordingHostConfig,
)
from evals.harness.stall_watch import DEFAULT_STALL_IDLE_SECONDS
from evals.recursion_depth.emit import (
    REPORT_JSON_NAME,
    assemble_report,
    derived_caveats,
    write_report,
)
from evals.recursion_depth.grading import SandboxUnitGrader
from evals.recursion_depth.journal import adopt_repaired_spend, read_recorded_cells
from evals.recursion_depth.manifest import (
    ModelPair,
    RecursionDepthManifest,
    load_manifest,
)
from evals.recursion_depth.models import (
    METRIC_CAVEAT,
    ORACLE_CAVEAT,
    RUN_STATE_CAVEATS,
    SIZING_CAVEAT,
    CellRecord,
    Provenance,
    RecursionDepthReport,
)
from evals.recursion_depth.planner import AgentSessionPlanner
from evals.recursion_depth.preflight import run_preflight
from evals.recursion_depth.provenance import capture_provenance
from evals.recursion_depth.runner import (
    SessionBudget,
    SweepContext,
    planned_cells,
    run_sweep,
)
from evals.recursion_depth.session import SessionLimits, SweepDeps
from evals.recursion_depth.spend_repair import (
    placed_units,
    repair_cell_spend,
    tokens_by_unit,
)
from evals.recursion_depth.staffing import build_roster
from evals.recursion_depth.tree import SpecBrief, arm_recursion, load_spec_brief
from evals.runner.execution import EVAL_TASK_PROJECT, seed_eval_project
from synthorg.config.loader import load_config
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_RECURSION_PREVIOUS_REPORT_UNREADABLE,
    EVALS_RECURSION_RECORD_START,
)
from synthorg.providers.family import model_named
from synthorg.settings.state import config_resolver_of, settings_service_of
from synthorg.workers._capability_policy_wiring import build_capability_policy

logger = get_logger(__name__)

_DEFAULT_MANIFEST: Final[Path] = Path("evals/recursion_depth/manifest.yaml")
_DEFAULT_COMPANY_CONFIG: Final[Path] = Path("evals/baselines/reference.yaml")
_DEFAULT_OUT_DIR: Final[Path] = Path("evals/recursion_depth/results")
_DEFAULT_WORK_ROOT: Final[Path] = Path(".recursion-depth/work")
_EPHEMERAL_PORT: Final[int] = 0
_LABEL: Final[str] = "recursion-depth"
#: Characters of the output-directory digest that name a run's scratch root.
#: Long enough that two output directories on one machine do not collide,
#: short enough to read in a path an operator is asked to inspect.
_SLUG_CHARS: Final[int] = 12


def _pair(pair: ModelPair) -> str:
    """Render one binding, family included.

    The family is shown because the independence claim rests on it and the plan
    is where an operator checks that claim before any spend. A connection name
    cannot settle it either way, so printing only the connection would show the
    reader everything except the fact that decides.

    Args:
        pair: The binding to render.

    Returns:
        ``provider/model_id (capability, family)``, or without the family where
        none is declared.
    """
    detail = str(pair.capability)
    if pair.family is not None:
        detail = f"{detail}, {pair.family}"
    return f"{pair.label} ({detail})"


def _resolved_family(pair: ModelPair, config: RootConfig) -> str | None:
    """The family the model actually answering for *pair* belongs to.

    Resolved down the same ladder the product's own family lookup uses
    (:func:`synthorg.providers.family.get_family`): the model's declared family
    where there is one, else the CONNECTION's. Reading only the model half
    answers ``None`` for a config that declares the family once on the
    connection and inherits it, and two ``None`` families satisfy the
    cross-family check by saying nothing rather than by differing, so a
    correlated judge records as independent.

    Returns:
        The family the model this pair aliases to belongs to, or ``None`` when
        neither the model nor its connection declares one.
    """
    provider = config.providers.get(pair.provider)
    if provider is None:
        return None
    model = model_named(provider, pair.model_id)
    if model is not None and model.metadata.family is not None:
        return model.metadata.family
    return provider.family


def check_declared_families(
    manifest: RecursionDepthManifest, config: RootConfig
) -> None:
    """Hold the manifest's independence claim to the models that answer it.

    The manifest declares a family per pair and the company config declares one
    per model, which is two owners for one fact. The manifest's copy is what
    the claim is checked against, and the config's copy names what runs, so a
    config aliasing both placeholders onto one organisation satisfies every
    check in the manifest and still produces a correlated judge.

    What is compared is the RELATION, never the names: the committed manifest
    ships deliberately vendor-agnostic placeholders, so its family strings
    cannot equal a real organisation's and testing for that would refuse every
    real recording. The claim those placeholders make is that the two pairs
    differ, and that survives aliasing intact.

    A config declaring no family for either model is not a disagreement, it is
    the config not saying, which leaves the manifest the only claim.

    Args:
        manifest: The recording matrix, carrying the declared families.
        config: The company config the pairs resolve against.

    Raises:
        RecursionDepthJudgeNotIndependentError: The manifest and the config
            disagree about whether the two pairs share a family.
    """
    if manifest.executor.family is None or manifest.reviewer.family is None:
        return
    claimed_distinct = manifest.executor.family != manifest.reviewer.family
    executor = _resolved_family(manifest.executor, config)
    reviewer = _resolved_family(manifest.reviewer, config)
    if executor is None or reviewer is None:
        return
    if (executor != reviewer) == claimed_distinct:
        return
    relation = "differ" if claimed_distinct else "match"
    msg = (
        f"the manifest claims the two pairs' families {relation}, but the "
        f"company config resolves {manifest.executor.label} to family "
        f"{executor!r} and {manifest.reviewer.label} to family {reviewer!r}; "
        f"the independence claim is checked against the manifest, so this "
        f"would record a decorrelation nobody achieved"
    )
    raise RecursionDepthJudgeNotIndependentError(msg)


def _reachable_caps(manifest: RecursionDepthManifest) -> str:
    """Which of the swept caps the ceiling can pay for, cheapest first.

    Returns:
        A human-readable summary of the prefix that fits, or a statement that
        not even the shallowest cap does.
    """
    arms = len(manifest.arms)
    spent = 0
    ordered = sorted(manifest.depths)
    afforded: list[int] = []
    for depth in ordered:
        spent += manifest.repetitions[depth] * arms * manifest.projected_sessions(depth)
        if spent > manifest.max_sessions:
            break
        afforded.append(depth)
    if not afforded:
        return "not even the shallowest cap fits"
    caps = ", ".join(str(depth) for depth in afforded)
    # The next SWEPT cap, not the next integer. `--depths 1,2,3,5` affording
    # three would otherwise name cap 4, which this sweep never runs, so the
    # operator is told the run stops somewhere it was never going.
    remaining = ordered[len(afforded) :]
    if not remaining:
        return f"caps {caps} fit; the whole matrix is affordable"
    return f"caps {caps} fit; the sweep is expected to stop inside cap {remaining[0]}"


def _ceiling_note(manifest: RecursionDepthManifest, projected: int) -> list[str]:
    """Say plainly when the matrix cannot be paid for, before anything is.

    The projection and the ceiling were printed on adjacent lines with nothing
    relating them, and this is the one screen where the spend decision is taken:
    a run was launched at a ceiling four times too small from exactly that
    reading, and it bought a whole planned tree, six built units and no
    measurement at all. Doing the comparison for the reader costs a line.

    Args:
        manifest: The recording matrix, already narrowed by any override.
        projected: What a full tree at the declared branching would cost.

    Returns:
        The lines to append, empty when the ceiling covers the projection.
    """
    if projected <= manifest.max_sessions:
        return []
    return [
        "",
        (
            f"  SHORTFALL     : the projection is {projected:,} sessions against a "
            f"ceiling of {manifest.max_sessions:,}, so a full tree at this "
            f"branching cannot be recorded in one sweep. The sweep stops at the "
            f"ceiling and reports what it measured; {_reachable_caps(manifest)}. "
            f"Narrow --depths, raise --max-sessions, or expect a partial curve."
        ),
    ]


def _projection_lines(manifest: RecursionDepthManifest, projected: int) -> list[str]:
    """Render what the matrix is projected to cost, and on what assumption.

    Args:
        manifest: The recording matrix.
        projected: Sessions a full tree costs across every planned cell.

    Returns:
        The projection block.
    """
    per_cell = {depth: manifest.projected_sessions(depth) for depth in manifest.depths}
    return [
        (
            f"  sessions      : {projected:,} for a full tree "
            "("
            + ", ".join(f"cap {d}: {per_cell[d]:,}/cell" for d in manifest.depths)
            + ")"
        ),
        (
            f"  assuming      : {manifest.projected_branching} subtasks per "
            f"planning session, so a cap of d holds "
            f"{manifest.projected_branching}^d leaves and plans at every node "
            f"above them. A planner that splits wider costs more than this."
        ),
        # The other half of the cost model, and the one the operator cannot
        # infer from the line above. The projection is the scenario a ceiling
        # is sized against; these decide whether a cell is STARTED at all, so a
        # figure that has drifted below what a cap really costs shows up as a
        # sweep that stops one cell short of the depth it was paid for.
        (
            "  expected      : "
            + ", ".join(
                f"cap {d}: {manifest.expected_sessions(d):,}/cell"
                for d in manifest.depths
            )
            + " (declared from measurement; a cap already recorded is priced "
            "from that run instead)"
        ),
        # Deliberately NOT called the expected bill. Each declared figure
        # carries margin, because it decides whether a cell may START and
        # refusing one that would have fit costs a measurement; summing them
        # therefore adds up twelve margins and reads high. What it answers is a
        # real question about the ceiling: could every planned cell still be
        # entered if all of them ran dear?
        (
            f"  declared cap  : "
            f"{sum(manifest.repetitions[d] * manifest.expected_sessions(d) for d in manifest.depths):,}"
            f" sessions if every cell hits its declared figure, which each"
            f" carries margin, so the run is expected to finish well inside it"
        ),
        (
            f"  ceiling       : {manifest.max_sessions} sessions, then the "
            "sweep stops and reports what it measured"
        ),
        # Named rather than left to be worked out. The two ceilings bound
        # different things and neither is a bill: an operator reading "3000
        # sessions" and "2.0 per session" is one multiplication away from the
        # number they actually care about, and printing the multiplication is
        # cheaper than discovering it afterwards.
        (
            f"  worst case    : {manifest.max_sessions * manifest.unit_cost_ceiling:.2f}"
            f" if every session spends its whole {manifest.unit_cost_ceiling:.2f}"
            " ceiling"
        ),
        # The money figure above is the one that reads as the bill and the one
        # that silently stops meaning anything: a flat-rate connection
        # attributes 0.0 to every call, so its cost ceiling never fires and
        # the worst case in money is 0.00 no matter how long the sweep runs.
        # Tokens are counted on every provider, so this line is the bound an
        # operator can rely on without first knowing how they are billed.
        (
            f"  token bound   : "
            f"{manifest.max_sessions * manifest.unit_token_ceiling:,} if every "
            f"session spends its whole {manifest.unit_token_ceiling:,}, and this "
            "is the bound that holds on a flat-rate connection, where the "
            "money ceiling above can never fire"
        ),
    ]


def describe_plan(manifest: RecursionDepthManifest, spec: SpecBrief) -> str:
    """Render the matrix a record run would execute.

    The session count is what a FULL tree costs at the declared branching, not
    a bound in either direction: a planner that stops short of the cap spends
    less, and one that branches wider spends more, neither of which the
    manifest can predict, which is exactly why ``max_sessions`` exists. It is
    derived from the TREE each cap admits rather than from the size of the
    matrix: a depth sweep's sessions come from the tree, so a matrix-shaped
    figure is the one an operator sizes a ceiling from and loses a paid run to.
    Against a real cap-3 cost of about 158 sessions PER CELL, a ceiling of 30
    bought a planned 85-leaf tree, six built units and nothing measured.

    The assumption is printed beside the figure rather than buried, because a
    model whose input is hidden reads as a measurement.

    Args:
        manifest: The recording matrix.
        spec: The specification the sweep builds.

    Returns:
        A human-readable plan.
    """
    cells = planned_cells(manifest)
    projected = sum(manifest.projected_sessions(cell.depth_cap) for cell in cells)
    lines = [
        "Recursion-depth recording plan",
        "",
        f"  specification : {spec.spec_id} ({len(spec.requirement_ids)} requirements)",
        f"  depth caps    : {', '.join(str(d) for d in manifest.depths)}",
        "  repetitions   : "
        + ", ".join(f"cap {d}: {manifest.repetitions[d]}" for d in manifest.depths),
        f"  arms          : {', '.join(arm.value for arm in manifest.arms)}",
        f"  executor      : {_pair(manifest.executor)}",
        f"  reviewer      : {_pair(manifest.reviewer)}",
        f"  independence  : {manifest.independence.value}",
        f"  merge attempts: {manifest.merge_attempts} (the SAME in every arm)",
        "",
        f"  runs          : {len(cells)}",
        *_projection_lines(manifest, projected),
    ]
    lines.extend(_ceiling_note(manifest, projected))
    caveat = manifest.caveat()
    if caveat is not None:
        lines.extend(["", f"  CAVEAT: {caveat}"])
    lines.extend(["", "Each session spends real provider tokens. Pass --record."])
    return "\n".join(lines)


async def _release(
    binder: HarnessBinder | None, run_work_root: Path, *, keep: bool
) -> None:
    """Give back what the sweep held, whether or not it finished.

    Every container has an owner that releases it on the ordinary path: a
    session releases its shell, a grading releases its suite runner, the oracle
    releases both of its own. This is the sweep, for whatever a raise left
    behind and for an owner whose release never ran because the failure landed
    before it.

    Nested, so releasing the containers and reclaiming the trees are two
    independent obligations rather than a sequence where the first one failing
    silently drops the second.

    Args:
        binder: The harness binder, or ``None`` if the host never stood up.
        run_work_root: Where this run built its trees.
        keep: Whether the trees stay. An unfinished sweep keeps them, because
            they are what ``--resume`` continues with: discarding them on the
            way out of a failure turns every part-built cell into one that has
            to be paid for again, which is the loss the journal exists to stop.
    """
    try:
        if binder is not None:
            await binder.release_all_sandboxes()
    finally:
        await _reclaim_workspaces(run_work_root, keep=keep)


async def _sweep_under(
    context: SweepContext,
    *,
    args: argparse.Namespace,
    manifest: RecursionDepthManifest,
    spec: SpecBrief,
) -> RecursionDepthReport:
    """Stamp what this run is measured against, then run it.

    The two belong together: provenance is captured from the tree the sweep is
    about to run against, so capturing it anywhere else is capturing it about a
    different commit than the one that produced the cells.

    Args:
        context: The bound sweep.
        args: The parsed command line.
        manifest: The recording matrix.
        spec: The specification the sweep builds.

    Returns:
        The report the sweep produced.
    """
    provenance = await asyncio.to_thread(
        partial(
            capture_provenance,
            repo_root=Path.cwd(),
            manifest_path=args.manifest,
            manifest=manifest,
            spec=spec,
            out_dir=args.out_dir,
        )
    )
    return await run_sweep(
        context, provenance=provenance, out_dir=args.out_dir, resume=args.resume
    )


async def _record(
    args: argparse.Namespace,
    *,
    manifest: RecursionDepthManifest,
    spec: SpecBrief,
    company_config: RootConfig,
) -> int:
    """Run the sweep for real and write the report.

    Returns:
        Process exit code.

    Raises:
        RecursionDepthNoCellsMeasuredError: Not one run was measured.
    """
    # Before the host, because everything it checks is a property of the
    # configuration or the machine and none of it becomes truer once a scratch
    # database, a gateway and a container are standing.
    await run_preflight(manifest=manifest, company_config=company_config)
    # Named after the output directory, because the journal there is what a
    # resume continues from and these trees are what it continues WITH: a
    # resume rebuilds each unit's path from this root, so a root it cannot
    # predict leaves every cell unable to find what was already built for it.
    # Two runs sharing an output directory are refused by the journal itself,
    # and two with different ones land on different roots, so concurrent
    # recordings still never reset each other's trees.
    run_work_root = args.work_root / f"run-{_recording_slug(args.out_dir)}"
    binder: HarnessBinder | None = None
    completed = False
    try:
        async with RecordingGatewayHost(
            _host_config(args, company_config=company_config, work_root=run_work_root)
        ) as host:
            binder = HarnessBinder(host=host)
            context = await _build_context(
                host,
                binder=binder,
                args=args,
                manifest=manifest,
                spec=spec,
                work_root=run_work_root,
            )
            _log_record_start(args, manifest=manifest, host=host)
            report = await _sweep_under(
                context, args=args, manifest=manifest, spec=spec
            )
            # Written inside the host's lifetime so a teardown that overruns
            # cannot discard a sweep that already cost real money to produce.
            paths = await asyncio.to_thread(write_report, report, args.out_dir)
            completed = True
    finally:
        await _release(
            binder, run_work_root, keep=args.keep_workspaces or not completed
        )
    print("report written: " + ", ".join(str(path) for path in paths))
    if not report.measured_cells:
        msg = (
            "every run recorded as unavailable, so the report measures "
            "nothing; the reasons are in the artifact just written"
        )
        raise RecursionDepthNoCellsMeasuredError(msg)
    return 0


def _host_config(
    args: argparse.Namespace, *, company_config: RootConfig, work_root: Path
) -> RecordingHostConfig:
    """Assemble the scratch backend the sweep dispatches through.

    Args:
        args: The parsed command line.
        company_config: The config the run boots against.
        work_root: This run's scratch root.

    Returns:
        The host configuration.
    """
    return RecordingHostConfig(
        company_config=company_config,
        scratch_dir=work_root / "host",
        label=_LABEL,
        bind_host=args.bind_host,
        bind_port=args.bind_port,
        container_host=args.container_host,
        sandbox_image=args.sandbox_image,
        sidecar_image=args.sidecar_image,
    )


async def _build_context(
    host: RecordingGatewayHost,
    *,
    binder: HarnessBinder,
    args: argparse.Namespace,
    manifest: RecursionDepthManifest,
    spec: SpecBrief,
    work_root: Path,
) -> SweepContext:
    """Arm the settings, staff the roster, and wire the sweep.

    Recursion is armed through the real settings service rather than handed to
    the decomposition service directly, so the sweep exercises the live read the
    product does.

    Returns:
        The wired context.

    Raises:
        RecursionDepthCapabilityUnresolvedError: No capability policy could be
            built, which leaves the gated arm unable to staff a reviewer.
        RecursionDepthCeilingUndeclaredError: A setting the sweep opens to its
            ceiling has none to read, so the arming cannot be trusted.
    """
    app_state = host.app_state
    await seed_eval_project(host.project_repo)
    await arm_recursion(settings_service_of(app_state), enabled=True)
    capability = await build_capability_policy(app_state)
    if capability is None:
        msg = (
            "no capability policy could be built, so no reviewer can be "
            "staffed and the gated arm would record escalations rather than "
            "verdicts; configure at least one provider"
        )
        raise RecursionDepthCapabilityUnresolvedError(msg)
    roster = await build_roster(
        executor=manifest.executor,
        reviewer=manifest.reviewer,
        capability=capability,
    )
    deps = _build_deps(
        host,
        binder=binder,
        # Under the run's own work root, so transcripts survive alongside the
        # trees `--keep-workspaces` leaves and are read against them.
        transcript_root=work_root / "transcripts",
        stall_idle_seconds=args.stall_notify_seconds,
        # The only place a model FAMILY is written down. A live identity has no
        # such field, so every per-unit record read `family: null` and the
        # cross_family claim the gated arm rests on was evidenced nowhere.
        declared_pairs=(manifest.executor, manifest.reviewer),
    )
    # What a PLANNING session gets. The units are bounded by
    # ``SweepContext.limits``, which reads the manifest itself; the two share a
    # spend ceiling and differ in turns, because the shipped decomposition
    # config caps a planner's turns and nothing caps a unit's.
    planner_limits = SessionLimits(
        max_turns=manifest.planner_max_turns,
        cost_ceiling=manifest.unit_cost_ceiling,
        token_ceiling=manifest.unit_token_ceiling,
    )
    return SweepContext(
        manifest=manifest,
        spec=spec,
        spec_dir=Path(manifest.spec_dir),
        work_root=work_root,
        deps=deps,
        roster=roster,
        planner=AgentSessionPlanner(
            deps=deps,
            roster=roster,
            executor=manifest.executor,
            limits=planner_limits,
            config_resolver=config_resolver_of(app_state),
        ),
        # The override is already folded into the manifest by `narrow`, so the
        # ceiling the run enforces is the one the plan printed.
        budget=SessionBudget(manifest.max_sessions),
        leaf_concurrency=args.leaf_concurrency,
    )


def _build_deps(
    host: RecordingGatewayHost,
    *,
    binder: HarnessBinder,
    transcript_root: Path,
    declared_pairs: tuple[ModelPair, ...],
    stall_idle_seconds: float = DEFAULT_STALL_IDLE_SECONDS,
) -> SweepDeps:
    """Bind every per-unit collaborator to the hosted gateway.

    The binder is passed in rather than built here so the recorder's own
    teardown can drain what grading and the oracle opened: both run outside the
    session context that drains the agent's sandboxes, so nothing else awaits
    their containers.

    Args:
        host: The hosted gateway a unit's calls cross.
        binder: What routes and authenticates each unit at that gateway.
        transcript_root: Where per-session transcripts are written.
        declared_pairs: The manifest's pairs, carrying the declared families.
        stall_idle_seconds: Idle time after which a unit is reported stalled.

    Returns:
        The wired :class:`SweepDeps`.
    """
    return SweepDeps(
        build_provider=binder.build_provider,
        build_tool_registry=binder.build_tool_registry,
        # Built per grading, never hoisted. A shared grader would share one
        # lifecycle strategy and therefore one warm container across units,
        # and unit N's graded run would see whatever unit N-1 left outside the
        # mount. owner_id pins the separation regardless, so that stays true
        # under a lifecycle this does not choose.
        build_grader=lambda workspace, *, owner: SandboxUnitGrader(
            sandbox=binder.build_sandbox(workspace.root, owner=owner),
            project_id=NotBlankStr(EVAL_TASK_PROJECT),
        ),
        build_sandbox=binder.build_sandbox,
        release_tools=binder.release_tool_sandboxes,
        # Every exchange with every model, one file per session. The chart
        # answers what each cell scored; this is the only thing that can
        # answer why, and none of it is recoverable after the run.
        transcripts=host.transcripts,
        transcript_root=transcript_root,
        open_run_ledger=binder.open_run_ledger,
        project_repo=host.project_repo,
        # A sweep unit is hours of work, so its conversation goes on disk turn
        # by turn and a session cut off by infrastructure is RESUMED rather
        # than re-run. Both or neither: the engine refuses one without the
        # other.
        checkpoint_repo=host.checkpoint_repo,
        heartbeat_repo=host.heartbeat_repo,
        stall_idle_seconds=stall_idle_seconds,
        on_stall=_print_stall,
        declared_pairs=declared_pairs,
    )


def _print_stall(label: str, idle_seconds: float) -> None:
    """Put a stalled unit where an operator watching the run will see it."""
    print(
        f"stalled: {label} has completed no LLM call for "
        f"{idle_seconds:.0f}s (the run continues)"
    )


def _log_record_start(
    args: argparse.Namespace,
    *,
    manifest: RecursionDepthManifest,
    host: RecordingGatewayHost,
) -> None:
    """State what is about to be spent, and where the container must reach."""
    logger.info(
        EVALS_RECURSION_RECORD_START,
        manifest=str(args.manifest),
        depths=list(manifest.depths),
        arms=[arm.value for arm in manifest.arms],
        planned_cells=manifest.planned_cells,
        max_sessions=args.max_sessions or manifest.max_sessions,
        independence=manifest.independence.value,
        work_root=str(args.work_root),
        # A container that cannot reach this is the failure mode hardest to
        # read from a run's output, so the address it was given is stated
        # once up front rather than inferred from a stack of timeouts.
        gateway_base_url=host.container_gateway_url,
        port=host.port,
    )


def _recording_slug(out_dir: Path) -> str:
    """A stable directory name for the recording written to *out_dir*.

    The output directory names the recording, because the journal that makes a
    sweep resumable lives in it. Hashed rather than embedded so an absolute
    path with separators, spaces or a drive letter still yields one path
    segment.

    Args:
        out_dir: Where the journal and the report are written.

    Returns:
        The slug.
    """
    resolved = str(out_dir.resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:_SLUG_CHARS]


async def _reclaim_workspaces(run_work_root: Path, *, keep: bool) -> None:
    """Remove this run's per-unit workspace trees.

    A sweep creates one tree per leaf and one per node, each written into by a
    coding agent. Nothing reuses a tree between COMPLETED runs, so retaining
    them grows disk monotonically; a maintainer inspecting what the sweep
    actually built is the one reason to keep them anyway. An unfinished run is
    the other case entirely: its trees are what the next ``--resume`` builds
    on.
    """
    if keep:
        print(f"workspaces kept for --resume: {run_work_root}")
        return
    await asyncio.to_thread(shutil.rmtree, run_work_root, ignore_errors=True)


def _positive_int(raw: str) -> int:
    """Parse a ceiling that has to be a real one.

    ``0`` is the value that matters here. ``argparse`` accepts it and every
    read of the option is ``args.max_sessions or manifest.max_sessions``, so a
    ceiling of zero is falsy, falls through to the manifest's own number, and
    spends against it: the operator asked for nothing to run and paid for a
    full sweep.

    Args:
        raw: The command-line text.

    Returns:
        The parsed count.

    Raises:
        argparse.ArgumentTypeError: The value is not a positive integer.
    """
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"expected a positive integer, got {raw!r}"
        raise argparse.ArgumentTypeError(msg) from exc
    if value < 1:
        msg = f"expected a positive integer, got {value}"
        raise argparse.ArgumentTypeError(msg)
    return value


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the recording CLI arguments.

    Returns:
        The parsed namespace.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--company-config", type=Path, default=_DEFAULT_COMPANY_CONFIG)
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT_DIR)
    parser.add_argument("--work-root", type=Path, default=_DEFAULT_WORK_ROOT)
    parser.add_argument(
        "--depths",
        default=None,
        help=(
            "Comma-separated depth caps to record, narrowing the manifest's "
            "own list. This is how a large sweep is staged: record the shallow "
            "end, read the curve forming, then pay for the deep end. Stages are "
            "CUMULATIVE: each one names every cap recorded so far, not only the "
            "new one, because the report holds exactly the caps this invocation "
            "planned. A journalled cell is replayed for free, so listing the "
            "earlier caps again costs nothing and omitting them emits a chart "
            "missing every cap already paid for."
        ),
    )
    parser.add_argument(
        "--repetitions",
        default=None,
        help=(
            "Override how many times a cap is recorded, as CAP:COUNT pairs "
            "(for example '4:1'). Only the caps named change. This is the "
            "lever for the deep end, where the bill is: a cap costs its "
            "branching to the power of its depth, so trading one repetition "
            "at the deepest cap buys back more time than anything else here. "
            "The committed counts are the experimental DESIGN (samples "
            "concentrated where the aggregation transition is expected), so "
            "they are overridden per run rather than edited, and the manifest "
            "digest a resume pins is taken over the file, which this does not "
            "touch."
        ),
    )
    parser.add_argument(
        "--leaf-concurrency",
        type=_positive_int,
        default=1,
        help=(
            "How many sibling leaves may build at once. Siblings are "
            "independent by construction, meeting only at the merge that "
            "assembles them, so this changes wall clock and nothing that is "
            "measured. It does NOT reduce quota: the same sessions run and "
            "spend the same tokens, just sooner. Not part of the provenance, "
            "so a run may be resumed at a different value."
        ),
    )
    parser.add_argument(
        "--max-sessions",
        type=_positive_int,
        default=None,
        help=(
            "Override the manifest's session ceiling. The sweep stops at it and "
            "reports what it measured rather than overrunning, because a depth "
            "sweep's session count is a product of branching factors the "
            "manifest cannot predict."
        ),
    )
    parser.add_argument(
        "--bind-host",
        default=None,
        help=(
            "Interface the recorder's own gateway listens on. Left unset, the "
            "narrowest address the sandbox can still reach is resolved."
        ),
    )
    parser.add_argument(
        "--bind-port",
        type=int,
        default=_EPHEMERAL_PORT,
        help="Port for the recorder's gateway; 0 takes an ephemeral one.",
    )
    parser.add_argument(
        "--container-host",
        default=DEFAULT_CONTAINER_HOST,
        help="Host the sandbox addresses the recorder by.",
    )
    parser.add_argument(
        "--sandbox-image",
        default=None,
        help=(
            "Override tools.sandbox_image, the image each unit's shell tool "
            "runs in. Nothing pulls, so this must name an image already "
            "present on the daemon."
        ),
    )
    parser.add_argument(
        "--sidecar-image",
        default=None,
        help="Override tools.sidecar_image, the egress-filtering sidecar.",
    )
    parser.add_argument(
        "--stall-notify-seconds",
        type=float,
        default=DEFAULT_STALL_IDLE_SECONDS,
        help=(
            "Idle time after which a unit is reported as stalled. A report, "
            "never a stop."
        ),
    )
    parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help=(
            "Leave every unit's tree on disk after the run. This is where the "
            "thing the sweep actually built ends up."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Continue the sweep already journalled in --out-dir: cells it "
            "measured are read back rather than paid for again, and cells it "
            "recorded as unavailable are attempted afresh. Without this a "
            "journal already in --out-dir is refused rather than overwritten. "
            "THE MANIFEST IS FROZEN once a journal exists: the header pins its "
            "digest along with the commit, the spec and both pairs, and a "
            "resume against a changed manifest is refused rather than mixing "
            "two matrices into one curve, which forfeits the planning already "
            "paid for. --depths, --repetitions and --max-sessions are the "
            "levers a resume still has, because each is folded into the "
            "manifest before the plan is printed rather than applied to the "
            "run, and none of them touches the file the digest is taken over. "
            "Editing manifest.yaml to run a cheaper matrix means starting a "
            "new --out-dir, and so does committing, because the identity pins "
            "the commit too."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--record",
        action="store_true",
        help="Execute the sweep against real providers (real spend).",
    )
    mode.add_argument(
        "--rescore",
        action="store_true",
        help=(
            "Re-emit the report from a finished recording's journal in "
            "--out-dir, spending nothing and running no provider call. Every "
            "input a report takes is already on disk, so a scoring change does "
            "not need the matrix run again. The RECORDING commit is carried "
            "across from the journal header rather than replaced by whatever "
            "HEAD is now, so the artefact cannot claim the sweep ran against "
            "code that did not exist when it ran."
        ),
    )
    parser.add_argument(
        "--repair-spend-from",
        type=Path,
        default=None,
        help=(
            "Rebuild the token column from a recorder log while re-scoring. "
            "For a recording whose sessions shared one process-wide cost sink "
            "swapped per session, where concurrent leaves could journal zero. "
            "The log holds one cost record per CALL, which no swap can "
            "scramble. Adds a caveat naming the repair, because a silently "
            "reconstructed spend column is worse than the fault. Refuses when "
            "the log places no call at all, rather than claim a repair that "
            "changed nothing."
        ),
    )
    args = parser.parse_args(argv)
    if args.repair_spend_from is not None and not args.rescore:
        # Only the rescore branch reads it, so without this the flag parses,
        # does nothing, and reports nothing: the operator is left believing the
        # token column was rebuilt on a run that never touched it.
        parser.error("--repair-spend-from is only meaningful with --rescore")
    return args


def parse_repetitions(
    raw: str | None, manifest: RecursionDepthManifest
) -> dict[int, int] | None:
    """Read a ``CAP:COUNT`` repetition override off the command line.

    Only the caps named are changed; the rest keep the manifest's own count, so
    an operator lowering the deep end does not silently reshape the shallow one.

    A cap the manifest does not sweep is REFUSED rather than ignored. The
    manifest validator only checks that every swept depth HAS a count, so an
    extra key validates cleanly and does nothing: ``--repetitions 41:1`` would
    be a typo for ``4:1`` that plans the full three repetitions and reports
    nothing wrong, which is the shape of mistake that gets discovered a day into
    a paid run.

    Args:
        raw: The command-line text, or ``None`` for no override.
        manifest: The loaded matrix, for the caps it actually sweeps.

    Returns:
        The overridden counts per cap, or ``None`` when nothing was asked for.

    Raises:
        ValueError: The text is malformed, names a cap the manifest does not
            sweep, or asks for a count below one.
    """
    if raw is None:
        return None
    wanted: dict[int, int] = {}
    for part in raw.split(","):
        entry = part.strip()
        if not entry:
            continue
        cap_text, separator, count_text = entry.partition(":")
        if not separator:
            msg = f"--repetitions wants CAP:COUNT pairs, got {entry!r}"
            raise ValueError(msg)
        try:
            cap, count = int(cap_text), int(count_text)
        except ValueError as exc:
            msg = f"--repetitions wants whole numbers, got {entry!r}"
            raise ValueError(msg) from exc
        if cap not in manifest.depths:
            msg = (
                f"--repetitions names cap {cap}, which this matrix does not "
                f"sweep: {list(manifest.depths)}"
            )
            raise ValueError(msg)
        if count < 1:
            msg = (
                f"--repetitions asks for {count} repetitions of cap {cap}; to "
                f"record none of it, leave the cap out of --depths"
            )
            raise ValueError(msg)
        wanted[cap] = count
    if not wanted:
        msg = "--repetitions was given no CAP:COUNT pair"
        raise ValueError(msg)
    return wanted


def narrow(
    manifest: RecursionDepthManifest,
    depths: str | None,
    max_sessions: int | None = None,
    repetitions: str | None = None,
) -> RecursionDepthManifest:
    """Narrow *manifest* to what this run records, and what it may spend.

    Every override is applied to the manifest itself rather than only to the
    run, because the plan is what an operator reads to decide whether to spend:
    a ceiling applied downstream of the plan prints the manifest's own figure
    beside the flags that were meant to lower it, which is the one moment the
    number is being relied on.

    None of them touches the manifest FILE, and the journal's identity pins the
    file's digest, so an override never turns a resumable matrix into a foreign
    one. What DOES is a commit, because the identity pins that too.

    Args:
        manifest: The loaded matrix.
        depths: Comma-separated caps, or ``None`` to keep the manifest's own.
        max_sessions: Session ceiling override, or ``None`` to keep the
            manifest's own.
        repetitions: ``CAP:COUNT`` pairs, or ``None`` to keep the manifest's
            own. This is the lever for the deep end, where the bill is: the
            committed counts are the experimental design (samples concentrated
            where ARIES puts the transition), and an operator trading one of
            them for a schedule should not have to edit that design into
            something the next reader inherits.

    Returns:
        The narrowed matrix.

    Raises:
        ValueError: A named cap is not in the manifest.
    """
    counts = parse_repetitions(repetitions, manifest)
    if depths is None and max_sessions is None and counts is None:
        return manifest
    override: dict[str, object] = {}
    if max_sessions is not None:
        override["max_sessions"] = max_sessions
    if counts is not None:
        override["repetitions"] = manifest.repetitions | counts
    if depths is None:
        return RecursionDepthManifest.model_validate(manifest.model_dump() | override)
    wanted = tuple(int(part) for part in depths.split(",") if part.strip())
    unknown = [cap for cap in wanted if cap not in manifest.depths]
    if unknown:
        msg = f"--depths names caps the manifest does not carry: {unknown}"
        raise ValueError(msg)
    # Re-validated rather than copied. `model_copy(update=...)` treats what it
    # is handed as trusted and runs no validator, and this value came off a
    # command line: `--depths 1,1` would plan the same cap twice and pay for it
    # twice, and `--depths ,` would narrow to nothing and record a sweep that
    # measured no cell at all. The manifest already refuses both, so the fix is
    # to go back through it rather than to restate its rules here.
    return RecursionDepthManifest.model_validate(
        manifest.model_dump() | {"depths": wanted} | override
    )


def _previous_caveats(out_dir: Path) -> tuple[str, ...]:
    """The caveats the last report written here carried.

    The only way a run-state caveat survives a re-score. The ceiling and quota
    ones are appended while the sweep runs and the journal does not hold them,
    so re-deriving from the cells alone would silently drop the sentence saying
    the matrix stopped early.

    An absent report is not an error: a re-score of a recording whose report
    never landed is exactly the case the mode is useful in, and the caller
    seeds the standing caveats instead. A report that is THERE and unreadable
    is a different fact and is logged, because it is the only copy of those
    sentences and returning the same empty tuple for both would quietly emit a
    report reading as though the sweep finished.

    Returns:
        The caveats, or empty when there is no readable previous report.
    """
    path = out_dir / REPORT_JSON_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    # `UnicodeDecodeError` derives from `ValueError`, not `OSError`, so a
    # truncated or half-written report escaped both handlers and aborted the
    # re-score that this function is documented to return empty for.
    except (OSError, UnicodeDecodeError) as exc:
        logger.error(
            EVALS_RECURSION_PREVIOUS_REPORT_UNREADABLE,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
    try:
        previous = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error(
            EVALS_RECURSION_PREVIOUS_REPORT_UNREADABLE,
            path=str(path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
    caveats = previous.get("caveats") if isinstance(previous, dict) else None
    if not isinstance(caveats, list):
        logger.error(
            EVALS_RECURSION_PREVIOUS_REPORT_UNREADABLE,
            path=str(path),
            error_type="SchemaMismatch",
            error="no caveats list",
        )
        return ()
    return tuple(str(entry) for entry in caveats)


def _repaired(
    out_dir: Path,
    provenance: Provenance,
    cells: list[CellRecord],
    *,
    log: Path,
) -> tuple[Provenance, list[CellRecord]]:
    """Rebuild the spend column from a recorder log and adopt it.

    Written back to the journal, not just to the report. A repair applied at
    scoring time leaves the artefact reproducible only by whoever still has the
    log, and the log is not a committed thing: the next re-score silently
    regresses the column to figures this recording's own caveat calls
    scrambled.

    Args:
        out_dir: Where the recording wrote its journal.
        provenance: What the recording says it is measured against.
        cells: The cells as journalled.
        log: The recorder log to attribute from.

    Returns:
        The provenance declaring a repaired column, and the repaired cells.

    Raises:
        RecursionDepthSpendRepairEmptyError: The log placed nothing on THIS
            recording, so the claim would sit beside figures nothing touched,
            which is the worse outcome of the two.
    """
    attributed = tokens_by_unit(log)
    # Against the recording's own units rather than against the attribution
    # alone: a log from a DIFFERENT recording parses perfectly and names cells
    # and units this one does not have, so it rewrites nothing while reading as
    # a full account of somebody's spend.
    if not placed_units(cells, attributed):
        msg = (
            f"{log} attributed no calls to any unit of this recording; the log "
            f"is not this recording's, or its rendering no longer parses"
        )
        raise RecursionDepthSpendRepairEmptyError(msg)
    return adopt_repaired_spend(
        out_dir, provenance=provenance, cells=repair_cell_spend(cells, attributed)
    )


def _rescore(out_dir: Path, *, repair_from: Path | None) -> int:
    """Re-emit a finished recording's report from its own journal.

    Spends nothing and calls no provider: the cells, the provenance and both
    curves are all recoverable from what the recording already wrote.

    Shipped as a mode here rather than run as a scratch script because an
    artefact produced by an uncommitted script is not reproducible by anyone,
    which is most of what a provenance block is for, and because a second path
    that builds a report is one refactor from disagreeing with the one the
    recorder uses.

    Args:
        out_dir: Where the recording wrote its journal and its report.
        repair_from: A recorder log to rebuild the token column from, or
            ``None`` to keep the journalled figures.

    Returns:
        Process exit code.
    """
    provenance, cells = read_recorded_cells(out_dir)
    # Rebuilt by default, carried only by declaration. The STANDING caveats
    # hold for every sweep this harness runs and the DERIVED ones are a pure
    # function of the cells, so re-deriving both is what gives an old report
    # this release's wording; carrying them instead froze a report at the
    # vocabulary it was written with, and re-scoring one twice left it holding
    # two wordings of the same caveat side by side.
    #
    # `RUN_STATE_CAVEATS` is the whole set that cannot be re-derived: they are
    # facts about how one run went (the session ceiling, a quota refusal, a
    # same-family judge) and the journal records cells, not why the sweep
    # stopped. Matching on the declared set rather than on "anything I do not
    # recognise" is what stops a retired sentence living for ever.
    #
    # The repair caveat is in neither group either, and for a different reason
    # from the two above: it is DERIVED, off the provenance the journal itself
    # carries. Stated instead by whoever typed the flag, it says nothing at all
    # on a later re-score of the same journal, or the opposite of what that
    # journal holds.
    if repair_from is not None:
        provenance, cells = _repaired(out_dir, provenance, cells, log=repair_from)
    caveats = [
        METRIC_CAVEAT,
        SIZING_CAVEAT,
        ORACLE_CAVEAT,
        *derived_caveats(cells, spend_source=provenance.spend_source),
        *(
            caveat
            for caveat in dict.fromkeys(_previous_caveats(out_dir))
            if caveat in RUN_STATE_CAVEATS
        ),
    ]
    report = assemble_report(
        provenance=provenance,
        cells=cells,
        caveats=caveats,
        planned_cells=len(cells),
    )
    written = write_report(report, out_dir)
    print("report written: " + ", ".join(str(path) for path in written))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)
    if args.rescore:
        # Before the manifest and the company config are touched. A re-score
        # needs neither, and --company-config defaults to the operator's own
        # gitignored pair file, so loading it here would fail on a clean
        # checkout, which is precisely the reproduction this mode exists for.
        return _rescore(args.out_dir, repair_from=args.repair_spend_from)
    manifest = narrow(
        load_manifest(args.manifest),
        args.depths,
        args.max_sessions,
        args.repetitions,
    )
    spec = load_spec_brief(Path(manifest.spec_dir))
    company_config = load_config(args.company_config)
    # Checked on the plan path too, not only before a record. The plan path is
    # what an operator runs first and is where they decide to spend, so a
    # contradiction found only under --record is found one decision too late.
    check_declared_families(manifest, company_config)

    if not args.record:
        # The plan path boots nothing, opens no port and starts no container.
        print(describe_plan(manifest, spec))
        return 0
    return asyncio.run(
        _record(args, manifest=manifest, spec=spec, company_config=company_config)
    )


if __name__ == "__main__":
    raise SystemExit(main())
