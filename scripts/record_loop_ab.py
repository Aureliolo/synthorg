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

Every loop dispatches through the LLM gateway, which is why ``--gateway-base-url``
is required to record: it is what puts all four loops on the same authoritative
cost ledger rather than a per-loop estimate. A recording that skipped it would
not be comparing like with like.
"""

import argparse
import asyncio
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Final
from uuid import uuid4

from evals.errors import LoopAbProviderMissingError
from evals.loader.briefs import load_brief_suite
from evals.loop_ab.emit import write_scoreboard
from evals.loop_ab.manifest import LoopAbManifest, TierEntry, load_manifest
from evals.loop_ab.provenance import capture_provenance
from evals.loop_ab.runner import LoopAbDeps, run_matrix
from evals.models.brief import Brief
from synthorg.config.loader import load_config
from synthorg.config.provider_schema import ProviderConfig
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_LOOP_AB_PROVIDER_MISSING,
    EVALS_LOOP_AB_RECORD_START,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.tools.file_system.delete_file import DeleteFileTool
from synthorg.tools.file_system.edit_file import EditFileTool
from synthorg.tools.file_system.read_file import ReadFileTool
from synthorg.tools.file_system.write_file import WriteFileTool
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.terminal.shell_command import ShellCommandTool

logger = get_logger(__name__)

_DEFAULT_MANIFEST: Final[Path] = Path("evals/loop_ab/manifest.yaml")
_DEFAULT_COMPANY_CONFIG: Final[Path] = Path("evals/baselines/reference.yaml")
_DEFAULT_OUT_DIR: Final[Path] = Path("evals/loop_ab/scoreboard")
_DEFAULT_WORK_ROOT: Final[Path] = Path(".loop-ab/work")


def _build_tool_registry(work_dir: Path) -> ToolRegistry:
    """Build the tool set a loop gets for one run, scoped to its workspace.

    Every tool is constructed against ``work_dir``, so a loop can only read and
    write inside the workspace it was given. The shell tool is included because
    two of the briefs expect the loop to run the code it is changing rather than
    reason about it from the source alone.

    The shell tool runs on a :class:`DockerSandbox`, never a subprocess one:
    this drives four loops with real LLM providers over authored brief and seed
    text, so the commands they emit are untrusted (``terminal`` sits in the
    project's ``_UNTRUSTED_EXEC_CATEGORIES``). Container isolation keeps that
    execution off the host running ``--record``, matching the OpenHands leg's
    own Docker requirement.

    Returns:
        The workspace-scoped :class:`ToolRegistry`.
    """
    return ToolRegistry(
        [
            ReadFileTool(workspace_root=work_dir),
            WriteFileTool(workspace_root=work_dir),
            EditFileTool(workspace_root=work_dir),
            DeleteFileTool(workspace_root=work_dir),
            ShellCommandTool(sandbox=DockerSandbox(workspace=work_dir.resolve())),
        ]
    )


def _provider_factory(
    *, company_config: Path, gateway_base_url: str
) -> Callable[[TierEntry], CompletionProvider]:
    """Build the per-tier provider factory, pointed at the LLM gateway.

    The tier's ``(provider, model_id)`` pair is resolved explicitly from the
    company config and the driver's ``base_url`` is overridden to the gateway,
    so every loop's dispatch is bound and metered identically.

    Returns:
        A callable building the provider for a tier.

    Raises:
        LoopAbProviderMissingError: The manifest names a provider absent from
            the company config.
    """
    root_config = load_config(company_config)
    configs: dict[str, ProviderConfig] = dict(root_config.providers)

    def _build(tier: TierEntry) -> CompletionProvider:
        base = configs.get(tier.provider)
        if base is None:
            logger.error(
                EVALS_LOOP_AB_PROVIDER_MISSING,
                tier=tier.tier,
                provider=tier.provider,
            )
            msg = (
                f"manifest tier {tier.tier!r} names provider {tier.provider!r}, "
                f"which is absent from {company_config}"
            )
            raise LoopAbProviderMissingError(msg)
        routed = base.model_copy(update={"base_url": gateway_base_url})
        registry = ProviderRegistry.from_config({tier.provider: routed})
        return registry.get(tier.provider)

    return _build


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
    """
    suite_root = Path(manifest.brief_suite)
    # A run-scoped scratch root so two concurrent ``--record`` invocations never
    # target the same workspace path: the per-cell reset (rmtree + copytree) is
    # only race-free within a single process, so cross-process isolation has to
    # come from a unique root rather than the shared default.
    run_work_root = args.work_root / f"run-{uuid4().hex[:12]}"

    logger.info(
        EVALS_LOOP_AB_RECORD_START,
        manifest=str(args.manifest),
        briefs=len(briefs),
        tiers=len(manifest.tiers),
        loops=len(manifest.loops),
        repetitions=manifest.repetitions,
        work_root=str(run_work_root),
    )

    deps = LoopAbDeps(
        build_provider=_provider_factory(
            company_config=args.company_config,
            gateway_base_url=args.gateway_base_url,
        ),
        build_tool_registry=_build_tool_registry,
        # The OpenHands loop authenticates to the gateway with a per-run bearer
        # minted by the *same* GatewaySigner instance the gateway verifies with.
        # That signer lives on the running API process's state, and a token
        # minted by any other instance is rejected, so a standalone script
        # cannot construct these deps: recording that leg requires driving the
        # matrix from inside a host that holds the signer. Left unwired here,
        # the OpenHands rows record themselves as unavailable with that reason
        # rather than vanishing from the comparison.
        openhands_loop_deps=None,
    )
    # ``capture_provenance`` shells out to git and ``write_scoreboard`` fsyncs to
    # disk; both are blocking, so they run off the event loop.
    provenance = await asyncio.to_thread(
        capture_provenance,
        repo_root=Path.cwd(),
        manifest_path=args.manifest,
        brief_suite_version=_suite_version(briefs),
    )
    scoreboard = await run_matrix(
        manifest=manifest,
        briefs=briefs,
        suite_root=suite_root,
        work_root=run_work_root,
        deps=deps,
        provenance=provenance,
    )
    json_path, md_path = await asyncio.to_thread(
        write_scoreboard, scoreboard, args.out_dir
    )
    print(f"scoreboard written: {json_path} and {md_path}")
    return 0


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
        "--gateway-base-url",
        default=None,
        help=(
            "Sandbox-reachable LLM gateway base URL, e.g. "
            "http://localhost:8000/api/v1/gateway/v1. Required with --record: "
            "routing every loop through it is what makes their recorded costs "
            "comparable."
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
        print(_describe_plan(manifest, len(briefs)))
        return 0
    if not args.gateway_base_url:
        print(
            "--gateway-base-url is required with --record: every loop must "
            "dispatch through the gateway so their costs are measured on the "
            "same authoritative ledger."
        )
        return 2
    return asyncio.run(_record(args, manifest=manifest, briefs=briefs))


if __name__ == "__main__":
    raise SystemExit(main())
