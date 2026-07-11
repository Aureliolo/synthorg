#!/usr/bin/env python3
"""Runner for the pre-push-only pure-Python gates.

The ~48 gates in ``_GATES`` are folded into this one runner rather than ~48
individual ``uv run python scripts/check_*.py`` pre-commit hooks. Each gate
is a whole-tree AST analysis costing several seconds -- parsing the ~6k-file
source tree dominates each gate's runtime -- so the runner fans them out
across a bounded reused-worker pool (``--jobs``; default ``min(12, cores)``,
override via ``PREPUSH_GATE_JOBS``). ``--jobs 1`` runs them serially in this
one process.

The pool is BOUNDED -- a handful of workers spawned once and reused for the
whole batch, not one process per gate -- so the concurrent process count
stays modest. That is the guard against the desktop-heap / STATUS_DLL_INIT_
FAILED (0xC0000142) pressure on Windows that a naive one-spawn-per-gate
fan-out would reintroduce. Each ``scripts/check_*.py`` file stays on disk so
the convention-gate-inventory meta-gate resolves each gate path, and CI's
``pre-commit run --all-files`` runs this one hook from the same config, so
local<->CI parity holds.

Each gate runs as if ``__main__`` in a fresh module namespace via
:func:`runpy.run_path`; its ``sys.exit(code)`` is caught and aggregated, and
every gate ALWAYS runs even if another fails, so one failure never masks the
rest. ``sys.argv``, the working directory, and ``sys.path`` are saved and
restored around each gate so a gate that reads ``sys.argv`` (the
``main(argv=None)`` shape) sees a clean argument list and a stray ``chdir``
or path mutation cannot leak into the next gate sharing its worker.

Gate contract: a gate registered in ``_GATES`` MUST be stateless with respect
to process globals beyond its own ``runpy`` namespace -- no permanent mutation
of ``sys.modules``, logging config, or signal handlers. Side-effect-free
static analysis (reads only) is the required posture; a gate that
monkey-patches a module would silently affect later gates sharing its worker.
"""

import argparse
import contextlib
import io
import os
import runpy
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Final

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Gate work is CPU-bound whole-tree AST analysis, so the 48 gates fan out
# across a bounded reused-worker pool. The cap is bounded (not one worker per
# gate) so the concurrent process count stays modest -- the desktop-heap /
# STATUS_DLL_INIT_FAILED pressure the single-process consolidation originally
# dodged. Workers are reused for the whole batch (no maxtasksperchild), so the
# total distinct spawns equal the pool size, created once at pool startup.
_DEFAULT_MAX_JOBS: Final[int] = 12
_FALLBACK_CPU: Final[int] = 8


def _default_jobs() -> int:
    """Return the default worker count.

    ``PREPUSH_GATE_JOBS`` overrides for machines that want to trade memory for
    speed (a worker per gate is CPU/memory-bound and imports ``synthorg``);
    otherwise the count is bounded by both the core count and the job cap.
    """
    override = os.environ.get("PREPUSH_GATE_JOBS")
    if override and override.strip().lstrip("-").isdigit():
        return max(1, int(override))
    cores = os.cpu_count() or _FALLBACK_CPU
    return max(1, min(_DEFAULT_MAX_JOBS, cores))


# Pre-push-only Python gates, folded from individual pre-commit hooks. Order
# mirrors .pre-commit-config.yaml for cross-referencing. Each entry is a
# scripts/check_*.py stem. Gates that need the changed-file list
# (pass_filenames=true), pass extra non-whole-tree args, run at the
# pre-commit stage too, or have a dedicated CI toolchain stay as their own
# hooks and are deliberately absent here.
_GATES: tuple[str, ...] = (
    "check_orphan_fixtures",
    "check_license_compat",
    "check_no_stdlib_logging",
    "check_no_stubs",
    "check_no_engine_worker_swallow",
    "check_no_ghost_wiring",
    "check_no_hardcoded_model_default",
    "check_mcp_capability_gap_documented",
    "check_runtime_reachability",
    "check_no_raw_playwright_imports",
    "check_forbidden_literals",
    "check_persistence_boundary",
    "check_currency_aggregation_invariant",
    "check_persistence_protocol_return_types",
    "check_dependency_inversion",
    "check_no_magic_numbers",
    "check_setting_to_startup_trace",
    "check_setting_restart_required_justified",
    "check_long_running_loops_have_kill_switch",
    "check_domain_error_hierarchy",
    "check_error_code_uniqueness",
    "check_mcp_admin_tool_guardrails",
    "check_handler_arguments_get",
    "check_no_os_environ_outside_bootstrap",
    "check_dead_api_endpoints",
    "check_dual_backend_test_parity",
    "check_frozen_model_extra_forbid",
    "check_boundary_typed",
    "check_provider_complete_chokepoint",
    "check_cost_scope_purpose",
    "check_prompt_class_metadata",
    "check_schema_drift",
    "check_convention_gate_inventory",
    "check_no_review_origin_in_code",
    "check_no_migration_framing",
    "check_docstring_completeness",
    "check_module_size_budget",
    "check_no_circular_imports",
    "check_architecture_drift",
    "check_module_depth",
    "check_protocol_documented",
    "check_no_module_level_io",
    "check_state_slice_immutability",
    "check_strategy_protocol_injection",
    "check_settings_namespace_complete",
    "check_feature_manifest",
    "check_no_implicit_state_attribute",
    "check_feature_index_freshness",
)


def _run_one(stem: str) -> tuple[int, str]:
    """Run one gate as ``__main__`` in-process; return ``(exit_code, detail)``.

    ``runpy.run_path`` re-executes the gate's module-level code in a fresh
    namespace and triggers its ``if __name__ == "__main__": sys.exit(main())``
    block, which we translate into an exit code. A non-``SystemExit``
    exception is isolated to this gate and reported as a failure so one
    crashing gate never aborts the batch.
    """
    script = _SCRIPTS / f"{stem}.py"
    if not script.is_file():
        return 1, f"gate script missing on disk: {script}"
    saved_argv = sys.argv
    saved_cwd = Path.cwd()
    saved_path = list(sys.path)
    sys.argv = [str(script)]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0, ""
        if isinstance(code, int):
            return code, ""
        return 1, str(code)
    except Exception:
        return 1, traceback.format_exc()
    else:
        # A gate that returns without calling sys.exit ran clean.
        return 0, ""
    finally:
        sys.argv = saved_argv
        # A gate that mutates ``sys.path`` (directly or via ``runpy.run_path``)
        # must not leak that change into the next gate; restore the snapshot so
        # each gate sees the same import path the runner started with.
        sys.path = saved_path
        # Best-effort cwd restore: if a gate chdir'd into a tempdir it then
        # removed, ``Path.cwd()`` raises and the process is left in an invalid
        # directory, so the next gate's relative-path reads break. Recover to a
        # known-good dir -- the saved cwd, or the repo root when the saved cwd
        # is itself gone -- rather than swallowing the error and advancing with
        # a broken cwd.
        try:
            current_cwd: Path | None = Path.cwd()
        except OSError:
            current_cwd = None
        target_cwd = saved_cwd if saved_cwd.is_dir() else _SCRIPTS.parent
        try:
            if current_cwd != target_cwd:
                os.chdir(target_cwd)
        except OSError:
            pass


def _run_gate(stem: str) -> tuple[str, int, float, str, str]:
    """Run one gate with output captured and timed; the pool-worker entry point.

    The gate's own ``stdout``/``stderr`` are captured into one buffer so a
    parallel run can attribute each gate's findings to it and report them in
    canonical order, rather than interleaving output from concurrent workers.
    Returns ``(stem, exit_code, elapsed_seconds, captured_output, detail)``.
    """
    buffer = io.StringIO()
    gate_start = time.monotonic()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code, detail = _run_one(stem)
    elapsed = time.monotonic() - gate_start
    return stem, code, elapsed, buffer.getvalue(), detail


def main(argv: list[str] | None = None) -> int:
    """Run every consolidated gate, then report the aggregate result.

    Gates run across a bounded reused-worker pool (``--jobs``); a job count of
    1 runs them serially in this process. Every gate always runs even if an
    earlier one fails, and results are reported in ``_GATES`` order regardless
    of completion order so the output is stable run to run.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jobs",
        type=int,
        default=_default_jobs(),
        help="worker processes to fan the gates across (1 = serial, in-process)",
    )
    args = parser.parse_args(argv)
    jobs = max(1, args.jobs)

    start = time.monotonic()
    results: dict[str, tuple[int, float, str, str]] = {}
    if jobs == 1:
        for stem in _GATES:
            _, code, elapsed, output, detail = _run_gate(stem)
            results[stem] = (code, elapsed, output, detail)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for stem, code, elapsed, output, detail in pool.map(_run_gate, _GATES):
                results[stem] = (code, elapsed, output, detail)

    failures: list[tuple[str, int, str, str]] = []
    for stem in _GATES:
        code, elapsed, output, detail = results[stem]
        status = "ok  " if code == 0 else "FAIL"
        print(f"  [{status}] {elapsed:5.1f}s  {stem}", file=sys.stderr)
        if code != 0:
            failures.append((stem, code, output, detail))
    total = time.monotonic() - start
    print(
        f"\nconsolidated pre-push gates: {len(_GATES)} run in {total:.1f}s "
        f"across {jobs} job(s), {len(failures)} failed",
        file=sys.stderr,
    )
    for stem, code, output, detail in failures:
        print(f"\n=== GATE FAILED: {stem} (exit {code}) ===", file=sys.stderr)
        if output.strip():
            print(output, file=sys.stderr)
        if detail.strip():
            print(detail, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
