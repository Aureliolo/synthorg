"""Record the inner execution-loop A/B scoreboard against real providers.

This is the entry point behind ``make loop-ab-record``. It runs every registered
loop over every brief on every model tier in the manifest, the configured number
of times, and writes the committed scoreboard artifact.

Two modes:

* **plan** (default): prints the matrix and what it would run, and spends
  nothing. Safe to run any time, and the way to see the size of the bill before
  committing to it.
* **record** (``--record``): the real measurement, against real providers, with
  real spend. Re-run it whenever the loops change and the previous scoreboard
  goes stale.

There is deliberately no offline replay mode that regenerates the artifact. Only
a real run produces scoreboard numbers, so a published ranking is always
something that actually happened; the harness itself is regression-tested
offline by ``tests/evals_spine/loop_ab``, which needs no spend.

Every loop dispatches through the LLM gateway, and the recorder hosts that
gateway itself. The OpenHands loop authenticates with a per-run bearer minted by
the *same* signer the gateway verifies with, so borrowing someone else's backend
is exactly the configuration that cannot work; owning the process that holds the
signer is what makes that leg recordable at all, and it puts both loops on one
authoritative cost ledger rather than a per-loop estimate.
"""

import argparse
import asyncio
import hashlib
import shutil
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Final
from uuid import uuid4

from evals.errors import (
    LoopAbGatewayUnavailableError,
    LoopAbNoCellsMeasuredError,
)
from evals.loader.briefs import load_brief_suite
from evals.loop_ab.binding import CellBinder
from evals.loop_ab.emit import write_scoreboard
from evals.loop_ab.host import (
    DEFAULT_CONTAINER_HOST,
    LoopAbGatewayHost,
    LoopAbHostConfig,
)
from evals.loop_ab.manifest import LoopAbManifest, load_manifest
from evals.loop_ab.models import Scoreboard
from evals.loop_ab.preflight import run_preflight
from evals.loop_ab.provenance import capture_provenance
from evals.loop_ab.runner import LoopAbDeps, run_matrix
from evals.loop_ab.stall_watch import DEFAULT_STALL_IDLE_SECONDS
from evals.models.brief import Brief
from synthorg.config.loader import load_config
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_LOOP_AB_RECORD_START,
    EVALS_LOOP_AB_WORKSPACES_RECLAIMED,
)

logger = get_logger(__name__)

_DEFAULT_MANIFEST: Final[Path] = Path("evals/loop_ab/manifest.yaml")
_DEFAULT_COMPANY_CONFIG: Final[Path] = Path("evals/baselines/reference.yaml")
_DEFAULT_OUT_DIR: Final[Path] = Path("evals/loop_ab/scoreboard")
_DEFAULT_WORK_ROOT: Final[Path] = Path(".loop-ab/work")
_EPHEMERAL_PORT: Final[int] = 0


def _describe_plan(manifest: LoopAbManifest, brief_count: int) -> str:
    """Render the matrix a record run would execute.

    Returns:
        A human-readable plan.
    """
    total = manifest.planned_runs * brief_count
    lines = [
        "Loop A/B recording plan",
        "",
        f"  loops       : {', '.join(manifest.loops)}",
        f"  tiers       : {', '.join(t.tier for t in manifest.tiers)}",
        f"  briefs      : {brief_count}",
        f"  repetitions : {manifest.repetitions}",
        "",
        f"  total runs  : {total}",
        "",
        "Each run spends real provider tokens. Pass --record to execute.",
    ]
    return "\n".join(lines)


async def _record(
    args: argparse.Namespace,
    *,
    manifest: LoopAbManifest,
    briefs: tuple[Brief, ...],
) -> int:
    """Run the matrix for real and write the scoreboard.

    The already-loaded *manifest* and *briefs* are passed in from ``main`` so the
    YAML is parsed once rather than re-read here.

    Returns:
        Process exit code.

    Raises:
        LoopAbNoCellsMeasuredError: The matrix completed with nothing measured.
            The scoreboard is written first, so the unavailable reasons survive
            for reading, but exiting successfully on an empty one would present
            a file that looks like a result.
    """
    # A run-scoped scratch root so two concurrent ``--record`` invocations never
    # target the same workspace path: the per-cell reset (rmtree + copytree) is
    # only race-free within a single process, so cross-process isolation has to
    # come from a unique root rather than the shared default.
    run_work_root = args.work_root / f"run-{uuid4().hex[:12]}"
    company_config = load_config(args.company_config)
    await run_preflight(manifest=manifest, company_config=company_config)

    host_config = LoopAbHostConfig(
        company_config=company_config,
        scratch_dir=run_work_root / "host",
        bind_host=args.bind_host,
        bind_port=args.bind_port,
        container_host=args.container_host,
        openhands_image=args.openhands_image,
        sandbox_image=args.sandbox_image,
        sidecar_image=args.sidecar_image,
    )
    try:
        async with LoopAbGatewayHost(host_config) as host:
            _log_record_start(args, manifest=manifest, briefs=briefs, host=host)
            scoreboard = await _run_supervised(
                host,
                manifest=manifest,
                briefs=briefs,
                run_work_root=run_work_root,
                deps=_build_deps(host, stall_idle_seconds=args.stall_notify_seconds),
                manifest_path=args.manifest,
            )
            # Written inside the host's lifetime so a teardown that overruns
            # cannot discard a matrix that already cost real money to produce.
            json_path, md_path = await asyncio.to_thread(
                write_scoreboard, scoreboard, args.out_dir
            )
    finally:
        await _reclaim_workspaces(run_work_root, keep=args.keep_workspaces)
    print(f"scoreboard written: {json_path} and {md_path}")
    if not scoreboard.measured_rows:
        msg = (
            "every cell recorded as unavailable, so the scoreboard measures "
            "nothing; the reasons are in the artifact just written"
        )
        raise LoopAbNoCellsMeasuredError(msg)
    return 0


def _build_deps(
    host: LoopAbGatewayHost,
    *,
    stall_idle_seconds: float = DEFAULT_STALL_IDLE_SECONDS,
) -> LoopAbDeps:
    """Bind every per-cell collaborator to the hosted gateway.

    Args:
        host: The started recording host.
        stall_idle_seconds: Idle time after which a cell is reported stalled.

    Returns:
        The fully wired :class:`LoopAbDeps`.
    """
    binder = CellBinder(host=host)
    return LoopAbDeps(
        build_provider=binder.build_provider,
        build_tool_registry=binder.build_tool_registry,
        build_openhands_cell=binder.build_openhands_cell,
        open_cell_ledger=binder.open_cell_ledger,
        project_repo=host.project_repo,
        stall_idle_seconds=stall_idle_seconds,
        on_stall=_print_stall,
    )


def _print_stall(cell_label: str, idle_seconds: float) -> None:
    """Put a stalled cell where an operator watching the run will see it."""
    print(
        f"stalled: {cell_label} has completed no LLM call for "
        f"{idle_seconds:.0f}s (the run continues)"
    )


def _log_record_start(
    args: argparse.Namespace,
    *,
    manifest: LoopAbManifest,
    briefs: tuple[Brief, ...],
    host: LoopAbGatewayHost,
) -> None:
    """State what is about to be spent, and where the container must reach.

    Args:
        args: The parsed CLI arguments.
        manifest: The loaded recording manifest.
        briefs: The loaded brief suite.
        host: The started recording host.
    """
    logger.info(
        EVALS_LOOP_AB_RECORD_START,
        manifest=str(args.manifest),
        briefs=len(briefs),
        tiers=len(manifest.tiers),
        loops=len(manifest.loops),
        repetitions=manifest.repetitions,
        work_root=str(args.work_root),
        # A container that cannot reach these is the failure mode hardest to
        # read from a run's output, so the addresses it was given are stated
        # once up front rather than inferred from a stack of timeouts.
        gateway_base_url=host.container_gateway_url,
        mcp_base_url=host.container_mcp_url,
        port=host.port,
    )


async def _run_supervised(
    host: LoopAbGatewayHost,
    *,
    manifest: LoopAbManifest,
    briefs: tuple[Brief, ...],
    run_work_root: Path,
    deps: LoopAbDeps,
    manifest_path: Path,
) -> Scoreboard:
    """Run the matrix, abandoning it if the gateway it dials stops serving.

    A serving task that dies mid-matrix turns every remaining cell into a
    connection error, which the per-cell handler faithfully records as that
    loop's unavailable row: the run keeps paying for containers and turns while
    measuring nothing, and the real cause surfaces only at teardown. Racing the
    two surfaces it at the first cell instead.

    Args:
        host: The started recording host.
        manifest: The loaded recording manifest.
        briefs: The loaded brief suite.
        run_work_root: Root for this run's per-cell workspaces.
        deps: The wired per-cell collaborators.
        manifest_path: Path the manifest was loaded from, for provenance.

    Returns:
        The completed scoreboard.

    Raises:
        LoopAbGatewayUnavailableError: The gateway stopped serving mid-matrix.
    """
    # ``capture_provenance`` shells out to git, so it runs off the event loop.
    provenance = await asyncio.to_thread(
        partial(
            capture_provenance,
            repo_root=Path.cwd(),
            manifest_path=manifest_path,
            brief_suite_version=_suite_version(briefs),
            images=host.images,
        )
    )
    matrix = asyncio.ensure_future(
        run_matrix(
            manifest=manifest,
            briefs=briefs,
            suite_root=Path(manifest.brief_suite),
            work_root=run_work_root,
            deps=deps,
            provenance=provenance,
        )
    )
    serving = host.serving
    if serving is None:
        return await matrix
    await asyncio.wait({matrix, serving}, return_when=asyncio.FIRST_COMPLETED)
    if matrix.done():
        return await matrix
    matrix.cancel()
    with suppress(asyncio.CancelledError):
        await matrix
    # Retrieved here so the reason the listener died becomes the cause the
    # operator sees. Left unread it surfaces as a bare "Task exception was never
    # retrieved" at shutdown, or is re-raised by the stop() inside __aexit__
    # (which awaits the same task and only catches TimeoutError) and masks this.
    cause = None if serving.cancelled() else serving.exception()
    msg = "the recording host stopped serving before the matrix finished"
    raise LoopAbGatewayUnavailableError(msg) from cause


async def _reclaim_workspaces(run_work_root: Path, *, keep: bool) -> None:
    """Remove this run's per-cell workspace trees.

    One tree per cell is recreated from the seed fixture and then written into
    by a coding agent, so a matrix leaves behind whatever those agents installed
    or built. Nothing reuses a tree between runs (each run mints its own root),
    so retaining them grows disk monotonically for no benefit unless a
    maintainer is inspecting what a loop actually produced.

    Args:
        run_work_root: Root for this run's per-cell workspaces.
        keep: Leave the trees on disk for inspection.
    """
    if keep:
        print(f"workspaces kept: {run_work_root}")
        return
    await asyncio.to_thread(shutil.rmtree, run_work_root, ignore_errors=True)
    logger.info(EVALS_LOOP_AB_WORKSPACES_RECLAIMED, work_root=str(run_work_root))


def _suite_version(briefs: tuple[Brief, ...]) -> str:
    """Derive a stable digest of the brief suite measured.

    Returns:
        A ``sha256:``-prefixed digest of the sorted brief ids.
    """
    joined = "|".join(sorted(brief.brief_id for brief in briefs))
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


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
        "--bind-host",
        default=None,
        help=(
            "Interface the recorder's own gateway listens on. Left unset, the "
            "narrowest address the sandbox can still reach is resolved: host "
            "loopback under Docker Desktop, the bridge gateway under Docker "
            "Engine. Set it only to override that; the recorder serves the "
            "whole application, so a wide bind exposes more than the gateway."
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
        help=(
            "Host the OpenHands sandbox addresses the recorder by. The default "
            "is the alias the loop wiring gives the container's sidecar."
        ),
    )
    parser.add_argument(
        "--openhands-image",
        default=None,
        help=(
            "Override tools.openhands_image, to record against a locally built "
            "image rather than the published one."
        ),
    )
    parser.add_argument(
        "--sandbox-image",
        default=None,
        help=(
            "Override tools.sandbox_image, the image the native legs' shell "
            "tool runs in. Nothing pulls, so this must name an image already "
            "present on the daemon."
        ),
    )
    parser.add_argument(
        "--sidecar-image",
        default=None,
        help=(
            "Override tools.sidecar_image, the egress-filtering sidecar the "
            "OpenHands leg's pinned network needs."
        ),
    )
    parser.add_argument(
        "--stall-notify-seconds",
        type=float,
        default=DEFAULT_STALL_IDLE_SECONDS,
        help=(
            "Idle time after which a cell is reported as stalled. A report, "
            "never a stop: nothing here ends a run, because every model this "
            "records against may price at zero, which leaves the gateway's cost "
            "ceiling unable to fire and turn count the only real bound."
        ),
    )
    parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help=(
            "Leave each cell's workspace tree on disk after the run, to inspect "
            "what a loop actually produced. Off by default: nothing reuses them "
            "between runs, so they only accumulate."
        ),
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Execute the matrix against real providers (real spend).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)
    manifest = load_manifest(args.manifest)
    briefs = load_brief_suite(Path(manifest.brief_suite))

    if not args.record:
        # The plan path boots nothing, opens no port and starts no container.
        print(_describe_plan(manifest, len(briefs)))
        return 0
    return asyncio.run(_record(args, manifest=manifest, briefs=briefs))


if __name__ == "__main__":
    raise SystemExit(main())
