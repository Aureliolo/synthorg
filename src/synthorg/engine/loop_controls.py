"""The in-flight controls a loop carries, named once as a type.

Resume rebuilds the loop to attach a checkpoint callback the engine could not
supply at construction, and the rebuild has to hand every control across. A
rebuild that names those fields at the call site is a rebuild that drops
whichever control the next one adds, which is what this type exists to stop:
a specialised loop reads the base half back as one value and adds only its
own, rather than restating a list that has already gone stale once.

It lives beside the loop rather than inside it because a subclass the loop
knows nothing about is exactly the caller that needs it, and importing the
loop to reach a type describing its arguments would be a cycle.
"""

from typing import TypedDict

from synthorg.core.clock import Clock
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.background_job_watch import BackgroundJobWatcher
from synthorg.engine.compaction.protocol import CompactionCallback
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.loop_protocol import TurnObserver
from synthorg.engine.quality.classifier import StepQualityClassifier
from synthorg.engine.stagnation.protocol import StagnationDetector
from synthorg.settings.resolver_protocol import ConfigResolverProtocol


class LoopControls(TypedDict):
    """Every control a rebuilt loop carries over from the one it copies.

    Keyed exactly as the loop constructor accepts them, so the mapping
    unpacks straight into it.
    """

    approval_gate: ApprovalGate | None
    stagnation_detector: StagnationDetector | None
    compaction_callback: CompactionCallback | None
    steering_inbox: SteeringInbox | None
    step_classifier: StepQualityClassifier | None
    turn_observer: TurnObserver | None
    background_job_watcher: BackgroundJobWatcher | None
    config_resolver: ConfigResolverProtocol | None
    clock: Clock
