# module-kind: code
"""Task grounding classification for the build/test oracle.

Decides whether a task's completion REQUIRES build/test grounding. The
rule keys on the same ``artifacts_expected`` signal the NO_OP->FAILED
invariant already uses, so the oracle and that invariant agree on what a
"code task" is: a task is REQUIRED iff it declares (or produced) a CODE
or TESTS artifact. Everything else abstains, so a doc / plan / decision
task is never blocked by the oracle.
"""

from collections.abc import Iterable

from synthorg.core.artifact import ArtifactType
from synthorg.core.task import Task
from synthorg.engine.completion_oracle.build_test_models import GroundingRequirement

_GROUNDED_ARTIFACT_TYPES: frozenset[ArtifactType] = frozenset(
    {ArtifactType.CODE, ArtifactType.TESTS}
)
"""Artifact types whose presence means the task's output must build + test."""


def classify_grounding_requirement(
    task: Task,
    *,
    produced_artifact_types: Iterable[ArtifactType] = (),
) -> GroundingRequirement:
    """Classify whether ``task`` requires build/test grounding to be "done".

    A task is ``REQUIRED`` when it declares an expected CODE / TESTS
    artifact, or when it actually produced one (the ``produced_artifact_types``
    corroboration, supplied by the read layer where the produced-artifact
    query already runs). This catches both a well-specified code task and
    a code deliverable on an under-specified task, while a docs-only task
    classifies ``NOT_APPLICABLE`` and the oracle abstains.

    Args:
        task: The completing task.
        produced_artifact_types: Types of artifacts the task actually
            produced, when known. Empty at the gate (which anchors on the
            declared ``artifacts_expected``); populated by the read layer.

    Returns:
        ``REQUIRED`` when a CODE / TESTS artifact is declared or produced,
        else ``NOT_APPLICABLE``.
    """
    declared = {expected.type for expected in task.artifacts_expected}
    produced = set(produced_artifact_types)
    if (declared | produced) & _GROUNDED_ARTIFACT_TYPES:
        return GroundingRequirement.REQUIRED
    return GroundingRequirement.NOT_APPLICABLE
