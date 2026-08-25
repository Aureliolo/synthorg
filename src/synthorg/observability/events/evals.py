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
EVALS_HARNESS_PROVIDER_MISSING: Final[str] = "evals.harness.provider_missing"
EVALS_HARNESS_PROBE_CLEANUP_FAILED: Final[str] = "evals.harness.probe_cleanup_failed"
"""The preflight probe's single-use provider registry could not be released.
Logged at WARNING and carries whether the probe itself had already failed,
because the two cases differ in what the operator is told: a probe that already
has a verdict keeps it (the bad credential or unknown model is the actionable
fact, and a cleanup failure raised over it would erase that), while a cleanup
failure on its own is the only thing that went wrong and is raised."""
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
EVALS_HARNESS_HOST_ADMIN_PRESENT: Final[str] = "evals.harness.host_admin_present"
EVALS_HARNESS_BIND_HOST_RESOLVED: Final[str] = "evals.harness.bind_host_resolved"
EVALS_HARNESS_BEARER_MINTED: Final[str] = "evals.harness.bearer_minted"
EVALS_HARNESS_LEDGER_INSTALLED: Final[str] = "evals.harness.ledger_installed"
EVALS_HARNESS_CELL_STALLED: Final[str] = "evals.harness.cell_stalled"
EVALS_HARNESS_TRANSCRIPT_WRITE_FAILED: Final[str] = (
    "evals.harness.transcript_write_failed"
)
EVALS_HARNESS_WORKSPACES_RECLAIMED: Final[str] = "evals.harness.workspaces_reclaimed"
EVALS_HARNESS_WORKSPACE_PATH_ESCAPED: Final[str] = (
    "evals.harness.workspace_path_escaped"
)
EVALS_HARNESS_WORKSPACE_LINK_DROPPED: Final[str] = (
    "evals.harness.workspace_link_dropped"
)
EVALS_HARNESS_STALL_REPORT_FAILED: Final[str] = "evals.harness.stall_report_failed"
EVALS_HARNESS_RECORD_JOURNALLED: Final[str] = "evals.harness.record_journalled"
EVALS_HARNESS_RECORD_REPLAYED: Final[str] = "evals.harness.record_replayed"
EVALS_HARNESS_JOURNAL_RESUMED: Final[str] = "evals.harness.journal_resumed"
EVALS_HARNESS_JOURNAL_TRUNCATED: Final[str] = "evals.harness.journal_truncated"
EVALS_HARNESS_SANDBOXES_RELEASED: Final[str] = "evals.harness.sandboxes_released"
EVALS_HARNESS_SANDBOX_RELEASE_FAILED: Final[str] = (
    "evals.harness.sandbox_release_failed"
)

# Recursion-depth sweep: does gating every merge hold off aggregation collapse?
EVALS_RECURSION_ORACLE_RUN: Final[str] = "evals.recursion_depth.oracle_run"
EVALS_RECURSION_UNIT_STARTED: Final[str] = "evals.recursion_depth.unit_started"
"""A sweep unit is about to open a session that spends real provider tokens.
Logged at DEBUG before the dispatch, because its INFO sibling
``EVALS_RECURSION_UNIT_EXECUTED`` only fires once the session RETURNS: a run
killed mid-session (a quota refusal, a wall-clock kill, an operator stopping
it) otherwise leaves no record of which unit was in flight when the money was
spent."""
EVALS_RECURSION_UNIT_EXECUTED: Final[str] = "evals.recursion_depth.unit_executed"
EVALS_RECURSION_UNIT_RESUMED: Final[str] = "evals.recursion_depth.unit_resumed"
"""A unit was cut off by an infrastructure failure and its session continued.

Logged at WARNING rather than DEBUG because it is the one place the operator
learns that a session did not run in one piece. The spend is real either way
and the unit reports one turn count, so without this line a resumed unit is
indistinguishable from a unit that simply took longer, and a provider having a
bad hour looks like a model that reasons at length."""
EVALS_RECURSION_UNIT_FAILED_SPEND: Final[str] = (
    "evals.recursion_depth.unit_failed_spend"
)
"""What a session had already spent when it raised rather than returned.

A raising session builds no outcome, so this figure reaches no cell record, no
journal row and no report: the log line IS the only place it is written down. A
provider call that recorded cost and then failed has still been paid for, and
without this the sweep's own ledger reads that money as never spent."""
EVALS_RECURSION_MERGE_ATTEMPTED: Final[str] = "evals.recursion_depth.merge_attempted"
EVALS_RECURSION_MERGE_GATED: Final[str] = "evals.recursion_depth.merge_gated"
EVALS_RECURSION_MERGE_PARKED: Final[str] = "evals.recursion_depth.merge_parked"
EVALS_RECURSION_TREE_BUILT: Final[str] = "evals.recursion_depth.tree_built"
EVALS_RECURSION_CELL_RECORDED: Final[str] = "evals.recursion_depth.cell_recorded"
EVALS_RECURSION_CELL_UNAVAILABLE: Final[str] = "evals.recursion_depth.cell_unavailable"
EVALS_RECURSION_RECORD_START: Final[str] = "evals.recursion_depth.record_start"
EVALS_RECURSION_REPORT_EMITTED: Final[str] = "evals.recursion_depth.report_emitted"
EVALS_RECURSION_SESSION_CEILING: Final[str] = "evals.recursion_depth.session_ceiling"
EVALS_RECURSION_QUOTA_EXHAUSTED: Final[str] = "evals.recursion_depth.quota_exhausted"
EVALS_RECURSION_CLAIM_UNRESOLVED: Final[str] = "evals.recursion_depth.claim_unresolved"
EVALS_RECURSION_NO_CELLS: Final[str] = "evals.recursion_depth.no_cells_measured"
EVALS_RECURSION_SYSTEMIC_FAILURE: Final[str] = "evals.recursion_depth.systemic_failure"
EVALS_RECURSION_GRADED: Final[str] = "evals.recursion_depth.graded"
EVALS_RECURSION_PREFLIGHT_PASSED: Final[str] = "evals.recursion_depth.preflight_passed"
EVALS_RECURSION_PLAN_RETRIED: Final[str] = "evals.recursion_depth.plan_retried"

EVALS_RECURSION_PLAN_FAILED: Final[str] = "evals.recursion_depth.plan_failed"
"""One planning attempt produced no tree, and what it had already spent.

Logged where the failure happens rather than only where it is caught, because
by the time the runner files the cell the attempt's own execution id, the depth
cap it was planning to, and the spend it booked on the way out are all several
frames behind the exception."""
EVALS_RECURSION_SETTINGS_ARMED: Final[str] = "evals.recursion_depth.settings_armed"
"""Which coordination settings a sweep put in force before it began measuring.

The sweep deliberately runs the product under settings no deployment ships, so
what it measured is only interpretable against the values it armed. A cell
killed by one of those ceilings reports that it produced no tree and nothing
else, and the ceilings are otherwise recoverable only by reading the harness
source at the commit the run used."""
EVALS_RECURSION_PLAN_BOOKING_FAILED: Final[str] = (
    "evals.recursion_depth.plan_booking_failed"
)
"""Booking a failed attempt's spend raised, and the money went unrecorded.

Its own event rather than a field on the failure above, because the two are
independent facts about one attempt: the planning failure is why the cell has
no tree, and this is why the cost the cell already incurred is missing from the
report. Only a log records it, since the exception is deliberately swallowed so
the planning failure stays the one the runner classifies on."""
EVALS_RECURSION_SPEND_DEDUPED: Final[str] = "evals.recursion_depth.spend_deduped"
"""A session's ledger held more than one account of its calls, and one was read.

With a gateway hosted, every call crosses it and it stamps ``PRODUCTIVE``, so
anything else on the ledger is a second account of a call already counted; a
live run journalled a planning unit at twice what it spent. Dropping is logged
rather than silent because the dropped set is either that duplicate or a call
that never crossed the gateway, and afterwards only this line tells them apart:
these session rows are the sweep's spend ledger of record."""
EVALS_RECURSION_SPEND_ALL_DROPPED: Final[str] = (
    "evals.recursion_depth.spend_all_dropped"
)
"""No account of a session's calls carried the category the gateway stamps.

Not the dedupe above: preferring one account of a call presumes another
survives, and here none did, so the whole ledger is counted instead and the
spend is right either way. Loud regardless, because the premise that every call
crosses the hosted gateway did not hold for this session, and that is a fact
about the run's wiring which nothing else would report."""
EVALS_RECURSION_SPEND_EMPTY: Final[str] = "evals.recursion_depth.spend_empty"
"""A session collected no records at all, so it journalled itself as free.

The sibling above covers a ledger whose accounts were all DROPPED; this covers
one that held nothing to drop, which reaches the same zero by a different route
and went unreported for a whole run. A session that took turns and recorded
nothing is spend that happened and was never written down, and these rows are
the only ledger there is."""
EVALS_RECURSION_SPEND_REPAIRED: Final[str] = "evals.recursion_depth.spend_repaired"
"""A recording's spend column was rebuilt from its own per-call log.

Re-scoring an old recording whose per-session ledger was scrambled by
concurrency. Logged because a repaired figure is a provenance claim: a token
column silently reconstructed is worse than the fault it corrects."""
EVALS_RECURSION_SPEND_UNCLAIMED: Final[str] = "evals.recursion_depth.spend_unclaimed"
"""The repair found calls that no journalled unit claimed.

Their spend is real and is attributed to nothing, which is the same class of
loss the repair exists to undo, so it is reported rather than dropped in
silence. Expected in one case only: a log captured while the run was still
appending to it."""
EVALS_RECURSION_LEAF_FAILURE_MASKED: Final[str] = (
    "evals.recursion_depth.leaf_failure_masked"
)
"""Sibling leaves failed together and only one of them could propagate.

Siblings are built concurrently and are deliberately not cancelled when one
fails, so a round can end holding several failures while a caller can only be
told about one. The rest are real and would otherwise vanish at the point the
first is raised, taking with them the evidence that a round failed broadly
rather than once."""
EVALS_RECURSION_PREVIOUS_REPORT_UNREADABLE: Final[str] = (
    "evals.recursion_depth.previous_report_unreadable"
)
"""A report was sitting where a re-score reads its predecessor, and would not
parse.

Absent is ordinary and silent: the mode exists for a recording whose report
never landed. Present and unreadable is not, because a run-state caveat (the
session ceiling, a quota refusal) is appended while the loop runs and the
journal does not hold it, so that file is the only copy. Silence here re-emits
a report reading as though the sweep finished."""
EVALS_RECURSION_SPEND_LOG_MALFORMED: Final[str] = (
    "evals.recursion_depth.spend_log_malformed"
)
"""The repair read lines it could not parse, and placed none of them.

Separate from the unclaimed sibling, which knows what it lost and how much: a
line whose fields do not match carries a call the repair cannot even size, so
it is missing from both the attributed total and the unclaimed one. Nothing
else would show it, and a repair that quietly read half a log would still emit
a report claiming the column was rebuilt."""
EVALS_RECURSION_CELL_JOURNALLED: Final[str] = "evals.recursion_depth.cell_journalled"
EVALS_RECURSION_RESUMED: Final[str] = "evals.recursion_depth.resumed"
EVALS_RECURSION_CELL_REPLAYED: Final[str] = "evals.recursion_depth.cell_replayed"
EVALS_RECURSION_JOURNAL_TRUNCATED: Final[str] = (
    "evals.recursion_depth.journal_truncated"
)
EVALS_RECURSION_CELL_CONTINUED: Final[str] = "evals.recursion_depth.cell_continued"
EVALS_RECURSION_CELL_RESTARTED: Final[str] = "evals.recursion_depth.cell_restarted"
