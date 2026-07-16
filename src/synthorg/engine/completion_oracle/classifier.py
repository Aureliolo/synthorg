# module-kind: code
"""Task grounding classification for the build/test oracle.

Decides whether a task's completion REQUIRES build/test grounding. The rule
reads the same ``artifacts_expected`` field the NO_OP->FAILED invariant checks
(``engine/task_sync.py``) -- a declared CODE / TESTS artifact means grounding
is required -- and additionally fails closed on the task ``type``: a
code-producing ``DEVELOPMENT`` task is grounded on its type alone, so an agent
cannot dodge the oracle by declaring no artifacts. A doc / plan / research task
(no code artifact, non-code type) is NOT_APPLICABLE and the oracle abstains, so
it is never blocked.
"""

from typing import Final

from synthorg.core.artifact import ArtifactType
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.engine.completion_oracle.build_test_models import GroundingRequirement

_GROUNDED_ARTIFACT_TYPES: Final[frozenset[ArtifactType]] = frozenset(
    {ArtifactType.CODE, ArtifactType.TESTS}
)
"""Artifact types whose presence means the task's output must build + test."""

_GROUNDED_TASK_TYPES: Final[frozenset[TaskType]] = frozenset({TaskType.DEVELOPMENT})
"""Task work types that inherently produce code and must build + test even
when no CODE / TESTS artifact was declared: a development task is grounded on
its type alone, so an agent cannot dodge the oracle by omitting the artifact
declaration (fail-closed)."""


def classify_grounding_requirement(task: Task) -> GroundingRequirement:
    """Classify whether ``task`` requires build/test grounding to be "done".

    Fails CLOSED on the two independent code signals: a declared CODE / TESTS
    artifact, OR a code-producing task ``type`` (``DEVELOPMENT``). Anchoring on
    the declared ``artifacts_expected`` keeps the gate verdict and the
    read-layer re-source in agreement (both call this classifier), while the
    type check closes the bypass where a development task ships no artifact
    declaration. A doc / plan / research task with no code artifact is
    NOT_APPLICABLE and the oracle abstains, so it is never blocked.

    Returns:
        ``REQUIRED`` when a CODE / TESTS artifact is declared or the task is a
        code-producing type, else ``NOT_APPLICABLE``.
    """
    declared = {expected.type for expected in task.artifacts_expected}
    if declared & _GROUNDED_ARTIFACT_TYPES or task.type in _GROUNDED_TASK_TYPES:
        return GroundingRequirement.REQUIRED
    return GroundingRequirement.NOT_APPLICABLE
