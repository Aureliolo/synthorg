#!/usr/bin/env python3
"""Single-process runner for the pre-push-only pure-Python gates.

Every gate listed here previously ran as its own
``uv run python scripts/check_*.py`` pre-commit hook. On Windows that meant
~40 sequential process spawns per push, each re-paying interpreter startup
and (for the gates that touch ``synthorg``) the multi-second package import,
and each adding to the desktop-heap pressure that surfaces as
``STATUS_DLL_INIT_FAILED`` (0xC0000142) once a loaded box can no longer
initialise a new process.

This runner executes every gate in ONE process via :func:`runpy.run_path`,
so the spawn count collapses from ~40 to 1 and the ``synthorg`` import is
paid once (cached in ``sys.modules`` for every later gate). Each gate runs
as if ``__main__`` in a fresh module namespace; its ``sys.exit(code)`` is
caught and aggregated. Every gate ALWAYS runs even if an earlier one fails,
so one failure never masks the rest -- matching pre-commit's
run-everything-then-report behaviour. ``sys.argv`` and the working directory
are saved/restored around each gate so a gate that reads ``sys.argv`` (the
``main(argv=None)`` shape) sees a clean argument list and a stray ``chdir``
cannot leak into the next gate.

The individual ``scripts/check_*.py`` files stay on disk (the
convention-gate-inventory meta-gate verifies each gate path exists), and CI's
``pre-commit run --all-files`` runs this single hook from the same config, so
local<->CI parity is preserved.

Gate contract: because gates share one process, a gate registered in
``_GATES`` MUST be stateless with respect to process globals beyond its own
``runpy`` namespace -- no permanent mutation of ``sys.modules``, logging
config, or signal handlers. Side-effect-free static analysis (reads only)
is the required posture; a gate that monkey-patches a module would silently
affect every later gate.
"""

import os
import runpy
import sys
import time
import traceback
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

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
    "check_no_ghost_wiring",
    "check_runtime_reachability",
    "check_no_raw_playwright_imports",
    "check_forbidden_literals",
    "check_persistence_boundary",
    "check_currency_aggregation_invariant",
    "check_persistence_protocol_return_types",
    "check_dependency_inversion",
    "check_no_magic_numbers",
    "check_setting_to_startup_trace",
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


def main() -> int:
    """Run every consolidated gate, then report the aggregate result."""
    failures: list[tuple[str, int, str]] = []
    start = time.monotonic()
    for stem in _GATES:
        gate_start = time.monotonic()
        code, detail = _run_one(stem)
        elapsed = time.monotonic() - gate_start
        status = "ok  " if code == 0 else "FAIL"
        print(f"  [{status}] {elapsed:5.1f}s  {stem}", file=sys.stderr)
        if code != 0:
            failures.append((stem, code, detail))
    total = time.monotonic() - start
    print(
        f"\nconsolidated pre-push gates: {len(_GATES)} run in {total:.1f}s, "
        f"{len(failures)} failed",
        file=sys.stderr,
    )
    for stem, code, detail in failures:
        print(f"\n=== GATE FAILED: {stem} (exit {code}) ===", file=sys.stderr)
        if detail.strip():
            print(detail, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
