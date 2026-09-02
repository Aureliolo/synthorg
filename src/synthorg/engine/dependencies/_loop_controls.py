# module-kind: declarative
"""The in-flight controls the execution loop consults at a turn boundary.

Grouped because they share one failure mode: each is consulted between
turns and each is silent when absent, so a loop running without the whole
set looks exactly like one running with it. Naming them together is what
makes "which of these is this engine running without" one question with one
answer.
"""

from dataclasses import dataclass

from synthorg.engine.background_job_watch import BackgroundJobWatcher
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.quality.classifier import StepQualityClassifier
from synthorg.engine.stagnation.protocol import StagnationDetector


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineLoopControls:
    """What runs between turns.

    Attributes:
        stagnation_detector: Watches for a loop going nowhere, or ``None``
            when ``stagnation.strategy`` selects no detector.
        compaction_callback: Evicts history once the context fill
            threshold is reached. ``None`` means history is monotonic and
            re-sent whole every turn.
        step_classifier: Scores each step's quality, or ``None``.
        steering_inbox: Reads operator directives at safe boundaries, or
            ``None`` when no project brain backs this engine.
        background_job_watcher: Nudges a run whose background job has gone
            stale, or ``None``.
    """

    stagnation_detector: StagnationDetector | None
    compaction_callback: CompactionCallback | None
    step_classifier: StepQualityClassifier | None
    steering_inbox: SteeringInbox | None
    background_job_watcher: BackgroundJobWatcher | None


__all__ = ["EngineLoopControls"]
