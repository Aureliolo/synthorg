#!/usr/bin/env python3
"""Runner for the pre-push-only pure-Python gates.

The ~48 gates in ``_GATES`` are folded into this one runner rather than ~48
individual ``uv run python scripts/check_*.py`` pre-commit hooks. Each gate is
a whole-tree static analysis (AST parse, tokenize, or regex over the tracked
source) costing several seconds -- reading and analysing the tree dominates
each gate's runtime -- so the runner fans them out across a bounded reused-
worker pool (``--jobs``; default ``min(12, cores)``, override via
``PREPUSH_GATE_JOBS``). ``--jobs 1`` runs them serially in this one process.

The pool is BOUNDED -- a handful of workers reused across the whole batch, not
one process per gate -- so the concurrent process count stays modest. That is
what avoids the desktop-heap / STATUS_DLL_INIT_FAILED (0xC0000142) pressure on
Windows that an unbounded 48-way fan-out would cause. Each ``scripts/check_*.py``
file stays on disk so the convention-gate-inventory meta-gate resolves each gate
path, and CI's ``pre-commit run --all-files`` runs this one hook from the same
config, so local<->CI parity holds.

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
import multiprocessing
import os
import runpy
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Final

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Each gate is a whole-tree static analysis (AST parse, tokenize, or regex over
# the tracked source) costing several seconds, so the 48 gates fan out across a
# bounded reused-worker pool. Bounding the pool (rather than one process per
# gate) keeps the concurrent process count modest, which avoids the desktop-heap
# / STATUS_DLL_INIT_FAILED (0xC0000142) pressure on Windows that an unbounded
# 48-way fan-out would cause. Workers are reused for the whole batch
# (``max_tasks_per_child`` left at its default), so one worker handles many
# gates over its lifetime rather than respawning per gate.
_DEFAULT_MAX_JOBS: Final[int] = 12
_FALLBACK_CPU: Final[int] = 8
# Wall-clock ceiling for the whole gate batch: gates are static analysis that
# finishes in seconds, so this is a safety net that fails the push loudly if a
# gate wedges, rather than hanging every push forever (the sibling pytest
# runner carries the same watchdog shape).
_BATCH_TIMEOUT_SECONDS: Final[float] = 600.0


def _default_jobs() -> int:
    """Return the default worker count.

    ``PREPUSH_GATE_JOBS`` overrides for machines that want to trade memory for
    speed (a worker per gate is CPU/memory-bound and imports ``synthorg``);
    otherwise the count is bounded by both the core count and the job cap.
    """
    override = os.environ.get("PREPUSH_GATE_JOBS")
    if override is not None and override.strip():
        cleaned = override.strip()
        if cleaned.lstrip("-").isdigit():
            return max(1, int(cleaned))
        print(
            f"run_prepush_python_gates: ignoring PREPUSH_GATE_JOBS={override!r} "
            "(not an integer); using the core-count default.",
            file=sys.stderr,
        )
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
    "check_no_provider_auto_pick",
    "check_gateway_explicit_binding",
    "check_credentialed_mcp_governed",
    "check_governed_destructive_tools",
    "check_forge_repo_scoped",
    "check_chat_inbound_fenced",
    "check_mcp_server_config_pinned",
    "check_mcp_self_consumer_scoped",
    "check_mcp_capability_gap_documented",
    "check_runtime_reachability",
    "check_output_boundaries_guarded",
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


def _recover_cwd(saved_cwd: Path) -> None:
    """Restore the working directory after a gate ran.

    A gate that chdir'd into a tempdir it then removed leaves ``Path.cwd()``
    raising and the process in an invalid directory, so the next gate sharing
    this worker gets broken relative-path reads. Recover to *saved_cwd*, or the
    repo root when the saved cwd is itself gone, rather than advancing broken.
    """
    try:
        current_cwd: Path | None = Path.cwd()
    except OSError:
        current_cwd = None
    target_cwd = saved_cwd if saved_cwd.is_dir() else _SCRIPTS.parent
    with contextlib.suppress(OSError):
        if current_cwd != target_cwd:
            os.chdir(target_cwd)


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
    except MemoryError, RecursionError:
        # Resource exhaustion is not a gate violation; let it propagate so an
        # OOM under parallel load surfaces distinctly (crashing the worker,
        # handled by the pool caller) instead of masquerading as a gate that
        # "failed" with a traceback.
        raise
    except Exception:
        return 1, traceback.format_exc()
    else:
        # A gate that returns without calling sys.exit ran clean.
        return 0, ""
    finally:
        sys.argv = saved_argv
        # A gate that mutates ``sys.path`` (directly or via ``runpy.run_path``)
        # must not leak that change into the next gate sharing this worker;
        # restore the snapshot so each gate sees the same import path.
        sys.path = saved_path
        _recover_cwd(saved_cwd)


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


_GateResult = tuple[int, float, str, str]


def _report_gate(stem: str, code: int, elapsed: float) -> None:
    """Print one gate's live status line as it completes."""
    status = "ok  " if code == 0 else "FAIL"
    print(f"  [{status}] {elapsed:5.1f}s  {stem}", file=sys.stderr)


def _run_serial() -> dict[str, _GateResult]:
    """Run every gate in this process, reporting each as it finishes."""
    results: dict[str, _GateResult] = {}
    for stem in _GATES:
        _, code, elapsed, output, detail = _run_gate(stem)
        results[stem] = (code, elapsed, output, detail)
        _report_gate(stem, code, elapsed)
    return results


def _run_pooled(jobs: int) -> tuple[dict[str, _GateResult], bool]:
    """Run gates across a bounded worker pool; report each as it lands.

    Returns ``(results, crashed)``. A worker dying (the STATUS_DLL_INIT_FAILED /
    OOM class the pool is bounded to avoid) breaks the whole pool, and a wedged
    gate trips the batch timeout: both are caught here and turned into a loud,
    attributed failure (naming the gates that never reported) rather than an
    unhandled ``BrokenProcessPool`` traceback or an indefinite hang. Gates that
    did report keep their results so their findings still surface.
    """
    results: dict[str, _GateResult] = {}
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(_run_gate, stem): stem for stem in _GATES}
        try:
            for future in as_completed(futures, timeout=_BATCH_TIMEOUT_SECONDS):
                stem, code, elapsed, output, detail = future.result()
                results[stem] = (code, elapsed, output, detail)
                _report_gate(stem, code, elapsed)
        except (BrokenProcessPool, TimeoutError) as exc:
            # A gate whose future raised is already ``done()`` yet never made it
            # into ``results``, so derive the unreported set from ``results``
            # membership rather than ``Future.done()`` (which would under-count).
            unfinished = sorted(stem for stem in _GATES if stem not in results)
            # A wedged worker never exits on its own, and both
            # ``shutdown(cancel_futures=True)`` and the context-manager exit
            # block waiting on it -- which would defeat the batch timeout and
            # hang the push. Terminate the pool's worker processes (this
            # runner spawns no other children) so the shutdown returns at once.
            for child in multiprocessing.active_children():
                child.terminate()
            pool.shutdown(wait=False, cancel_futures=True)
            reason = (
                "a gate worker crashed (BrokenProcessPool)"
                if isinstance(exc, BrokenProcessPool)
                else f"the {_BATCH_TIMEOUT_SECONDS:.0f}s batch timeout fired"
            )
            print(
                f"\nconsolidated pre-push gates: {reason}; "
                f"{len(unfinished)} gate(s) never reported: "
                f"{', '.join(unfinished)}.\n"
                "Re-run with PREPUSH_GATE_JOBS=1 to isolate the offending gate.",
                file=sys.stderr,
            )
            return results, True
    return results, False


def main(argv: list[str] | None = None) -> int:
    """Run every consolidated gate, then report the aggregate result.

    Gates run across a bounded reused-worker pool (``--jobs``); a job count of 1
    runs them serially in this process. Each gate's status prints live as it
    finishes (completion order under the pool, ``_GATES`` order when serial);
    the final failure detail is reported in ``_GATES`` order for stable output.
    A worker crash or the batch timeout fails the push loudly (see ``_run_pooled``)
    rather than masking which gates ran.
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
    crashed = False
    if jobs == 1:
        results = _run_serial()
    else:
        results, crashed = _run_pooled(jobs)

    failures = [
        (stem, results[stem][0], results[stem][2], results[stem][3])
        for stem in _GATES
        if stem in results and results[stem][0] != 0
    ]
    unreported = [stem for stem in _GATES if stem not in results]
    total = time.monotonic() - start
    tail = f", {len(unreported)} did not complete" if unreported else ""
    print(
        f"\nconsolidated pre-push gates: {len(results)}/{len(_GATES)} reported "
        f"in {total:.1f}s across {jobs} job(s), {len(failures)} failed{tail}",
        file=sys.stderr,
    )
    for stem, code, output, detail in failures:
        print(f"\n=== GATE FAILED: {stem} (exit {code}) ===", file=sys.stderr)
        if output.strip():
            print(output, file=sys.stderr)
        if detail.strip():
            print(detail, file=sys.stderr)
    return 1 if (failures or crashed or unreported) else 0


if __name__ == "__main__":
    sys.exit(main())
