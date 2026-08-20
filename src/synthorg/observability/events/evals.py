"""Golden-benchmark eval-spine event constants for structured logging.

Constants follow the ``evals.<subject>.<action>`` naming convention
and are passed as the first positional argument to structured log
calls. The ``evals`` prefix is distinct from ``eval.*`` (used by the
legacy ``eval_loop`` subsystem in this package) and matches the
top-level ``evals/`` package that owns the golden-benchmark suite.
"""

from typing import Final

EVALS_EXECUTABLE_TIMEOUT: Final[str] = "evals.executable.timeout"
EVALS_EXECUTABLE_TOOL_MISSING: Final[str] = "evals.executable.tool_missing"
EVALS_JUDGE_CALIBRATION_FAILED: Final[str] = "evals.judge.calibration_failed"
EVALS_BRIEF_RUN_COMPLETE: Final[str] = "evals.brief.run_complete"
EVALS_PURPOSE_INVOKED_FIELD_MISSING: Final[str] = (
    "evals.brief.purpose_invoked_field_missing"
)
EVALS_SUITE_RUN_START: Final[str] = "evals.suite.run_start"
EVALS_SUITE_RUN_COMPLETE: Final[str] = "evals.suite.run_complete"
EVALS_BENCHMARK_SCORE_RECORDED: Final[str] = "evals.benchmark.score_recorded"
EVALS_WORKSPACE_SEEDED: Final[str] = "evals.workspace.seeded"
EVALS_BRIEF_WALL_CLOCK_EXCEEDED: Final[str] = "evals.brief.wall_clock_exceeded"
EVALS_BRIEF_SUITE_PATH_REJECTED: Final[str] = "evals.brief_suite.path_rejected"
EVALS_HARNESS_DIRTY_TREE: Final[str] = "evals.harness.dirty_tree"
EVALS_LOOP_AB_RECORD_START: Final[str] = "evals.loop_ab.record_start"
EVALS_LOOP_AB_RUN_RECORDED: Final[str] = "evals.loop_ab.run_recorded"
EVALS_LOOP_AB_LOOP_UNAVAILABLE: Final[str] = "evals.loop_ab.loop_unavailable"
EVALS_HARNESS_PROVIDER_MISSING: Final[str] = "evals.harness.provider_missing"
EVALS_LOOP_AB_SCOREBOARD_EMITTED: Final[str] = "evals.loop_ab.scoreboard_emitted"
EVALS_HARNESS_HOST_STARTED: Final[str] = "evals.harness.host_started"
EVALS_HARNESS_HOST_STOPPED: Final[str] = "evals.harness.host_stopped"
EVALS_HARNESS_HOST_START_FAILED: Final[str] = "evals.harness.host_start_failed"
EVALS_HARNESS_HOST_STOP_TIMED_OUT: Final[str] = "evals.harness.host_stop_timed_out"
EVALS_HARNESS_HOST_SECRETS_INSTALLED: Final[str] = (
    "evals.harness.host_secrets_installed"
)
EVALS_HARNESS_HOST_IMAGES_INSTALLED: Final[str] = "evals.harness.host_images_installed"
EVALS_HARNESS_IMAGE_UNRESOLVED: Final[str] = "evals.harness.image_unresolved"
EVALS_HARNESS_HOST_ADMIN_SEEDED: Final[str] = "evals.harness.host_admin_seeded"
EVALS_HARNESS_BIND_HOST_RESOLVED: Final[str] = "evals.harness.bind_host_resolved"
EVALS_HARNESS_BEARER_MINTED: Final[str] = "evals.harness.bearer_minted"
EVALS_HARNESS_LEDGER_INSTALLED: Final[str] = "evals.harness.ledger_installed"
EVALS_LOOP_AB_CELL_PARTIAL: Final[str] = "evals.loop_ab.cell_partial"
EVALS_HARNESS_CELL_STALLED: Final[str] = "evals.harness.cell_stalled"
EVALS_LOOP_AB_PREFLIGHT_PASSED: Final[str] = "evals.loop_ab.preflight_passed"
EVALS_LOOP_AB_PREFLIGHT_LATENCY: Final[str] = "evals.loop_ab.preflight_latency"
EVALS_HARNESS_TRANSCRIPT_WRITE_FAILED: Final[str] = (
    "evals.harness.transcript_write_failed"
)
EVALS_LOOP_AB_EVIDENCE_KEEP_FAILED: Final[str] = "evals.loop_ab.evidence_keep_failed"
EVALS_HARNESS_WORKSPACES_RECLAIMED: Final[str] = "evals.harness.workspaces_reclaimed"
EVALS_HARNESS_WORKSPACE_PATH_ESCAPED: Final[str] = (
    "evals.harness.workspace_path_escaped"
)
EVALS_HARNESS_STALL_REPORT_FAILED: Final[str] = "evals.harness.stall_report_failed"
EVALS_HARNESS_SANDBOXES_RELEASED: Final[str] = "evals.harness.sandboxes_released"
EVALS_HARNESS_SANDBOX_RELEASE_FAILED: Final[str] = (
    "evals.harness.sandbox_release_failed"
)

# Recursion-depth sweep: does gating every merge hold off aggregation collapse?
EVALS_RECURSION_ORACLE_RUN: Final[str] = "evals.recursion_depth.oracle_run"
EVALS_RECURSION_UNIT_EXECUTED: Final[str] = "evals.recursion_depth.unit_executed"
EVALS_RECURSION_MERGE_ATTEMPTED: Final[str] = "evals.recursion_depth.merge_attempted"
EVALS_RECURSION_MERGE_GATED: Final[str] = "evals.recursion_depth.merge_gated"
EVALS_RECURSION_MERGE_PARKED: Final[str] = "evals.recursion_depth.merge_parked"
EVALS_RECURSION_TREE_BUILT: Final[str] = "evals.recursion_depth.tree_built"
EVALS_RECURSION_CELL_RECORDED: Final[str] = "evals.recursion_depth.cell_recorded"
EVALS_RECURSION_CELL_UNAVAILABLE: Final[str] = "evals.recursion_depth.cell_unavailable"
EVALS_RECURSION_RECORD_START: Final[str] = "evals.recursion_depth.record_start"
EVALS_RECURSION_REPORT_EMITTED: Final[str] = "evals.recursion_depth.report_emitted"
EVALS_RECURSION_SESSION_CEILING: Final[str] = "evals.recursion_depth.session_ceiling"
