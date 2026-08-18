"""Run-recovery event name constants for observability."""

from typing import Final

RUN_RECOVERY_SWEEP_STARTED: Final[str] = "run_recovery.sweep.started"
RUN_RECOVERY_SWEEP_COMPLETE: Final[str] = "run_recovery.sweep.complete"
RUN_RECOVERY_SWEEP_FAILED: Final[str] = "run_recovery.sweep.failed"
RUN_RECOVERY_SWEEP_PAUSED: Final[str] = "run_recovery.sweep.paused"
RUN_RECOVERY_PLAN_RESUMED: Final[str] = "run_recovery.plan.resumed"
RUN_RECOVERY_PLAN_SKIPPED: Final[str] = "run_recovery.plan.skipped"
RUN_RECOVERY_PLAN_FAILED: Final[str] = "run_recovery.plan.failed"
RUN_RECOVERY_TASK_REQUEUED: Final[str] = "run_recovery.task.requeued"
RUN_RECOVERY_SCHEDULER_STARTED: Final[str] = "run_recovery.scheduler.started"
RUN_RECOVERY_SCHEDULER_STOPPED: Final[str] = "run_recovery.scheduler.stopped"
RUN_RECOVERY_SCHEDULER_FAILED: Final[str] = "run_recovery.scheduler.failed"
