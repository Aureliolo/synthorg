"""Evaluation loop event constants."""

from typing import Final

EVAL_LOOP_CYCLE_START: Final[str] = "eval.loop.cycle_start"
EVAL_LOOP_CYCLE_COMPLETE: Final[str] = "eval.loop.cycle_complete"
EVAL_LOOP_CYCLE_FAILED: Final[str] = "eval.loop.cycle_failed"
EVAL_LOOP_PATTERN_IDENTIFIED: Final[str] = "eval.loop.pattern_identified"
EVAL_LOOP_BENCHMARK_EXECUTED: Final[str] = "eval.loop.benchmark_executed"
EVAL_LOOP_BASELINE_LOADED: Final[str] = "eval.loop.baseline_loaded"
EVAL_LOOP_METRICS_COMPUTED: Final[str] = "eval.loop.metrics_computed"
EVAL_LOOP_AGENT_EVAL_FAILED: Final[str] = "eval.loop.agent_eval_failed"
EVAL_LOOP_BENCHMARK_FAILED: Final[str] = "eval.loop.benchmark_failed"
EVAL_LOOP_ACTION_PROPOSED: Final[str] = "eval.loop.action_proposed"
# Emitted at INFO when a cycle's identified corrective actions route to
# the training pipeline (gated by ``EvalLoopConfig.training_on_actions``).
EVAL_LOOP_TRAINING_TRIGGERED: Final[str] = "eval.loop.training_triggered"

# Startup-time config validation failure (module import bails out).
# Kept distinct from ``EVAL_LOOP_CYCLE_FAILED``, which denotes a
# runtime evaluation-cycle failure; operators can alert on this event
# separately to catch deploy-time drift.
EVAL_LOOP_CONFIG_DRIFT: Final[str] = "eval.loop.config_drift"
