# module-kind: code
"""Task grounding classification for the build/test oracle.

Decides whether a task's completion REQUIRES build/test grounding. The rule
reads the same ``artifacts_expected`` field the NO_OP->FAILED invariant checks
(``engine/task_sync.py``), but narrows it further: that invariant fires for a
WORK task declaring *any* expected artifact, whereas the oracle requires
grounding only when a CODE or TESTS artifact is declared. So the two agree that
a task is a WORK task, and the oracle additionally decides it is a *code* task.
A doc / plan / decision task (no CODE / TESTS declared) is NOT_APPLICABLE and
the oracle abstains, so it is never blocked.
"""

from typing import Final

from synthorg.core.artifact import ArtifactType
from synthorg.core.task import Task
from synthorg.engine.completion_oracle.build_test_models import GroundingRequirement

_GROUNDED_ARTIFACT_TYPES: Final[frozenset[ArtifactType]] = frozenset(
    {ArtifactType.CODE, ArtifactType.TESTS}
)
"""Artifact types whose presence means the task's output must build + test."""


def classify_grounding_requirement(task: Task) -> GroundingRequirement:
    """Classify whether ``task`` requires build/test grounding to be "done".

    Anchors on the task's declared ``artifacts_expected`` (the same signal the
    completion gate and the NO_OP->FAILED invariant both act on, so the gate's
    verdict and the read-layer re-source can never disagree on the same task).

    Returns:
        ``REQUIRED`` when a CODE / TESTS artifact is declared, else
        ``NOT_APPLICABLE``.
    """
    declared = {expected.type for expected in task.artifacts_expected}
    if declared & _GROUNDED_ARTIFACT_TYPES:
        return GroundingRequirement.REQUIRED
    return GroundingRequirement.NOT_APPLICABLE
