"""Evaluation loop event constants."""

from typing import Final

EVAL_LOOP_CYCLE_START: Final[str] = "eval.loop.cycle_start"
EVAL_LOOP_CYCLE_COMPLETE: Final[str] = "eval.loop.cycle_complete"
EVAL_LOOP_CYCLE_FAILED: Final[str] = "eval.loop.cycle_failed"
EVAL_LOOP_PATTERN_IDENTIFIED: Final[str] = "eval.loop.pattern_identified"
EVAL_LOOP_BENCHMARK_EXECUTED: Final[str] = "eval.loop.benchmark_executed"
EVAL_LOOP_BENCHMARK_ALREADY_REGISTERED: Final[str] = (
    "eval.loop.benchmark_already_registered"
)
EVAL_LOOP_BENCHMARK_NOT_FOUND: Final[str] = "eval.loop.benchmark_not_found"
EVAL_LOOP_BENCHMARK_STARTED: Final[str] = "eval.loop.benchmark_started"
# A single test case failed its agent run or grading; the benchmark run
# isolates it (scored as failed) and continues with the remaining cases.
EVAL_LOOP_BENCHMARK_CASE_FAILED: Final[str] = "eval.loop.benchmark_case_failed"
EVAL_LOOP_BASELINE_LOADED: Final[str] = "eval.loop.baseline_loaded"
EVAL_LOOP_METRICS_COMPUTED: Final[str] = "eval.loop.metrics_computed"
EVAL_LOOP_AGENT_EVAL_FAILED: Final[str] = "eval.loop.agent_eval_failed"
EVAL_LOOP_BENCHMARK_FAILED: Final[str] = "eval.loop.benchmark_failed"
EVAL_LOOP_ACTION_PROPOSED: Final[str] = "eval.loop.action_proposed"
# Emitted per action routed to a wired PatternActionDispatcher (INFO on
# dispatch, WARNING when the dispatcher raises).
EVAL_LOOP_ACTION_DISPATCHED: Final[str] = "eval.loop.action_dispatched"
# Emitted at INFO when a cycle's identified corrective actions route to
# the training pipeline (gated by ``EvalLoopConfig.training_on_actions``).
EVAL_LOOP_TRAINING_TRIGGERED: Final[str] = "eval.loop.training_triggered"

# Startup-time config validation failure (module import bails out).
# Kept distinct from ``EVAL_LOOP_CYCLE_FAILED``, which denotes a
# runtime evaluation-cycle failure; operators can alert on this event
# separately to catch deploy-time drift.
EVAL_LOOP_CONFIG_DRIFT: Final[str] = "eval.loop.config_drift"

# Periodic cycle-scheduler lifecycle (the background driver that runs
# ``run_cycle`` on a cadence; opt-in, off by default).
EVAL_LOOP_CYCLE_SCHEDULER_STARTED: Final[str] = "eval.loop.scheduler_started"
EVAL_LOOP_CYCLE_SCHEDULER_STOPPED: Final[str] = "eval.loop.scheduler_stopped"
EVAL_LOOP_CYCLE_SCHEDULER_FAILED: Final[str] = "eval.loop.scheduler_failed"
EVAL_LOOP_CYCLE_RAN: Final[str] = "eval.loop.scheduler_cycle_ran"
# A scheduler tick that ran no cycle because ``hr.eval_loop_cycle_paused``
# is set. Distinct from CYCLE_RAN so an operator alerting on cycle
# activity does not get false hits every tick while the loop is paused.
EVAL_LOOP_CYCLE_PAUSED: Final[str] = "eval.loop.scheduler_cycle_paused"
