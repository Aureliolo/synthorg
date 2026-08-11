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
EVALS_LOOP_AB_DIRTY_TREE: Final[str] = "evals.loop_ab.dirty_tree"
EVALS_LOOP_AB_RECORD_START: Final[str] = "evals.loop_ab.record_start"
EVALS_LOOP_AB_RUN_RECORDED: Final[str] = "evals.loop_ab.run_recorded"
EVALS_LOOP_AB_LOOP_UNAVAILABLE: Final[str] = "evals.loop_ab.loop_unavailable"
EVALS_LOOP_AB_PROVIDER_MISSING: Final[str] = "evals.loop_ab.provider_missing"
EVALS_LOOP_AB_SCOREBOARD_EMITTED: Final[str] = "evals.loop_ab.scoreboard_emitted"
EVALS_LOOP_AB_HOST_STARTED: Final[str] = "evals.loop_ab.host_started"
EVALS_LOOP_AB_HOST_STOPPED: Final[str] = "evals.loop_ab.host_stopped"
EVALS_LOOP_AB_HOST_START_FAILED: Final[str] = "evals.loop_ab.host_start_failed"
EVALS_LOOP_AB_HOST_STOP_TIMED_OUT: Final[str] = "evals.loop_ab.host_stop_timed_out"
EVALS_LOOP_AB_HOST_SECRETS_INSTALLED: Final[str] = (
    "evals.loop_ab.host_secrets_installed"
)
EVALS_LOOP_AB_HOST_ADMIN_SEEDED: Final[str] = "evals.loop_ab.host_admin_seeded"
EVALS_LOOP_AB_BIND_HOST_RESOLVED: Final[str] = "evals.loop_ab.bind_host_resolved"
EVALS_LOOP_AB_BEARER_MINTED: Final[str] = "evals.loop_ab.bearer_minted"
EVALS_LOOP_AB_LEDGER_INSTALLED: Final[str] = "evals.loop_ab.ledger_installed"
EVALS_LOOP_AB_CELL_PARTIAL: Final[str] = "evals.loop_ab.cell_partial"
EVALS_LOOP_AB_CELL_STALLED: Final[str] = "evals.loop_ab.cell_stalled"
EVALS_LOOP_AB_PREFLIGHT_PASSED: Final[str] = "evals.loop_ab.preflight_passed"
EVALS_LOOP_AB_PREFLIGHT_LATENCY: Final[str] = "evals.loop_ab.preflight_latency"
EVALS_LOOP_AB_TRANSCRIPT_WRITE_FAILED: Final[str] = (
    "evals.loop_ab.transcript_write_failed"
)
EVALS_LOOP_AB_EVIDENCE_KEEP_FAILED: Final[str] = "evals.loop_ab.evidence_keep_failed"
EVALS_LOOP_AB_WORKSPACES_RECLAIMED: Final[str] = "evals.loop_ab.workspaces_reclaimed"
