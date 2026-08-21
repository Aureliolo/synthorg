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
import shutil
from functools import partial
from pathlib import Path
from typing import Final
from uuid import uuid4

from evals.errors import (
    RecursionDepthCapabilityUnresolvedError,
    RecursionDepthJudgeNotIndependentError,
    RecursionDepthNoCellsMeasuredError,
)
from evals.harness.binding import HarnessBinder
from evals.harness.host import (
    DEFAULT_CONTAINER_HOST,
    RecordingGatewayHost,
    RecordingHostConfig,
)
from evals.harness.stall_watch import DEFAULT_STALL_IDLE_SECONDS
from evals.recursion_depth.emit import write_report
from evals.recursion_depth.grading import SandboxUnitGrader
from evals.recursion_depth.manifest import (
    ModelPair,
    RecursionDepthManifest,
    load_manifest,
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
from evals.recursion_depth.staffing import build_roster
from evals.recursion_depth.tree import SpecBrief, arm_recursion, load_spec_brief
from evals.runner.execution import EVAL_TASK_PROJECT, seed_eval_project
from synthorg.config.loader import load_config
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_RECORD_START
from synthorg.settings.state import config_resolver_of, settings_service_of
from synthorg.workers._capability_policy_wiring import build_capability_policy

logger = get_logger(__name__)

_DEFAULT_MANIFEST: Final[Path] = Path("evals/recursion_depth/manifest.yaml")
_DEFAULT_COMPANY_CONFIG: Final[Path] = Path("evals/baselines/reference.yaml")
_DEFAULT_OUT_DIR: Final[Path] = Path("evals/recursion_depth/results")
_DEFAULT_WORK_ROOT: Final[Path] = Path(".recursion-depth/work")
_EPHEMERAL_PORT: Final[int] = 0
_LABEL: Final[str] = "recursion-depth"


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

    Returns:
        The company config's family for the model this pair aliases to, or
        ``None`` when the provider, the model or its family is not declared
        there.
    """
    provider = config.providers.get(pair.provider)
    if provider is None:
        return None
    for model in provider.models:
        if pair.model_id in {model.id, model.alias}:
            return model.metadata.family
    return None


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


def describe_plan(manifest: RecursionDepthManifest, spec: SpecBrief) -> str:
    """Render the matrix a record run would execute.

    The session count is deliberately given as a floor rather than an estimate:
    it is a product of branching factors nobody can predict from the manifest,
    which is exactly why ``max_sessions`` exists.

    Args:
        manifest: The recording matrix.
        spec: The specification the sweep builds.

    Returns:
        A human-readable plan.
    """
    cells = planned_cells(manifest)
    floor = len(cells) * (1 + manifest.merge_attempts * 2)
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
        f"  merge attempts: {manifest.merge_attempts} (the SAME in both arms)",
        "",
        f"  runs          : {len(cells)}",
        (
            f"  sessions      : at least {floor}, and one per leaf and per "
            "node on top of that"
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
    caveat = manifest.caveat()
    if caveat is not None:
        lines.extend(["", f"  CAVEAT: {caveat}"])
    lines.extend(["", "Each session spends real provider tokens. Pass --record."])
    return "\n".join(lines)


async def _record(
    args: argparse.Namespace,
    *,
    manifest: RecursionDepthManifest,
    spec: SpecBrief,
) -> int:
    """Run the sweep for real and write the report.

    Returns:
        Process exit code.

    Raises:
        RecursionDepthNoCellsMeasuredError: Not one run was measured.
    """
    # A run-scoped scratch root so two concurrent ``--record`` invocations never
    # target the same workspace path: each unit's reset removes and re-copies a
    # whole tree, which is only race-free within one process.
    company_config = load_config(args.company_config)
    # Before the host, because everything it checks is a property of the
    # configuration or the machine and none of it becomes truer once a scratch
    # database, a gateway and a container are standing.
    await run_preflight(manifest=manifest, company_config=company_config)
    run_work_root = args.work_root / f"run-{uuid4().hex[:12]}"
    host_config = RecordingHostConfig(
        company_config=company_config,
        scratch_dir=run_work_root / "host",
        label=_LABEL,
        bind_host=args.bind_host,
        bind_port=args.bind_port,
        container_host=args.container_host,
        sandbox_image=args.sandbox_image,
        sidecar_image=args.sidecar_image,
    )
    binder: HarnessBinder | None = None
    try:
        async with RecordingGatewayHost(host_config) as host:
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
            provenance = await asyncio.to_thread(
                partial(
                    capture_provenance,
                    repo_root=Path.cwd(),
                    manifest_path=args.manifest,
                    manifest=manifest,
                    spec=spec,
                )
            )
            report = await run_sweep(context, provenance=provenance)
            # Written inside the host's lifetime so a teardown that overruns
            # cannot discard a sweep that already cost real money to produce.
            paths = await asyncio.to_thread(write_report, report, args.out_dir)
    finally:
        # Grading and the oracle open their own containers, and both run OUTSIDE
        # the session context whose exit drains the agent's. Left to that hook
        # alone, each grading container waits for the next unit's teardown and
        # the last ones are never reclaimed at all, which is the leak
        # release_tool_sandboxes exists to prevent, reintroduced by a second
        # producer.
        if binder is not None:
            await binder.release_tool_sandboxes()
        await _reclaim_workspaces(run_work_root, keep=args.keep_workspaces)
    print("report written: " + ", ".join(str(path) for path in paths))
    if not report.measured_cells:
        msg = (
            "every run recorded as unavailable, so the report measures "
            "nothing; the reasons are in the artifact just written"
        )
        raise RecursionDepthNoCellsMeasuredError(msg)
    return 0


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
        host, binder=binder, stall_idle_seconds=args.stall_notify_seconds
    )
    limits = SessionLimits(
        max_turns=manifest.unit_max_turns,
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
            limits=limits,
            config_resolver=config_resolver_of(app_state),
        ),
        # The override is already folded into the manifest by `narrow`, so the
        # ceiling the run enforces is the one the plan printed.
        budget=SessionBudget(manifest.max_sessions),
    )


def _build_deps(
    host: RecordingGatewayHost,
    *,
    binder: HarnessBinder,
    stall_idle_seconds: float = DEFAULT_STALL_IDLE_SECONDS,
) -> SweepDeps:
    """Bind every per-unit collaborator to the hosted gateway.

    The binder is passed in rather than built here so the recorder's own
    teardown can drain what grading and the oracle opened: both run outside the
    session context that drains the agent's sandboxes, so nothing else awaits
    their containers.

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
        build_grader=lambda workspace: SandboxUnitGrader(
            sandbox=binder.build_sandbox(workspace.root),
            project_id=NotBlankStr(EVAL_TASK_PROJECT),
        ),
        build_sandbox=binder.build_sandbox,
        release_tools=binder.release_tool_sandboxes,
        open_run_ledger=binder.open_run_ledger,
        project_repo=host.project_repo,
        stall_idle_seconds=stall_idle_seconds,
        on_stall=_print_stall,
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
        # A container that cannot reach these is the failure mode hardest to
        # read from a run's output, so the addresses it was given are stated
        # once up front rather than inferred from a stack of timeouts.
        gateway_base_url=host.container_gateway_url,
        mcp_base_url=host.container_mcp_url,
        port=host.port,
    )


async def _reclaim_workspaces(run_work_root: Path, *, keep: bool) -> None:
    """Remove this run's per-unit workspace trees.

    A sweep creates one tree per leaf and one per node, each written into by a
    coding agent. Nothing reuses a tree between runs, so retaining them grows
    disk monotonically unless a maintainer is inspecting what was built, which
    for the first working artefact in nine rounds is a real reason.
    """
    if keep:
        print(f"workspaces kept: {run_work_root}")
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
            "end, read the curve forming, then pay for the deep end."
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
        "--record",
        action="store_true",
        help="Execute the sweep against real providers (real spend).",
    )
    return parser.parse_args(argv)


def narrow(
    manifest: RecursionDepthManifest,
    depths: str | None,
    max_sessions: int | None = None,
) -> RecursionDepthManifest:
    """Narrow *manifest* to the depth caps *depths* names and *max_sessions*.

    Both overrides are applied to the manifest itself rather than only to the
    run, because the plan is what an operator reads to decide whether to spend:
    a ceiling applied downstream of the plan prints the manifest's own figure
    beside the flags that were meant to lower it, which is the one moment the
    number is being relied on.

    Args:
        manifest: The loaded matrix.
        depths: Comma-separated caps, or ``None`` to keep the manifest's own.
        max_sessions: Session ceiling override, or ``None`` to keep the
            manifest's own.

    Returns:
        The narrowed matrix.

    Raises:
        ValueError: A named cap is not in the manifest.
    """
    if depths is None and max_sessions is None:
        return manifest
    override: dict[str, object] = {}
    if max_sessions is not None:
        override["max_sessions"] = max_sessions
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)
    manifest = narrow(load_manifest(args.manifest), args.depths, args.max_sessions)
    spec = load_spec_brief(Path(manifest.spec_dir))
    # Checked on the plan path too, not only before a record. The plan path is
    # what an operator runs first and is where they decide to spend, so a
    # contradiction found only under --record is found one decision too late.
    check_declared_families(manifest, load_config(args.company_config))

    if not args.record:
        # The plan path boots nothing, opens no port and starts no container.
        print(describe_plan(manifest, spec))
        return 0
    return asyncio.run(_record(args, manifest=manifest, spec=spec))


if __name__ == "__main__":
    raise SystemExit(main())
