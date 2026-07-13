# module-kind: code
"""Shared projection from a free-text artifact spec to a typed declaration.

Both dispatch paths that mint child tasks from a decomposition -- the direct
:class:`~synthorg.engine.decomposition.service.DecompositionService` build and
the plan-review round-trip in ``plan_mapping`` -- infer the artifact type the
same way, so the fail-loud zero-artifact guard sees a consistent typed
declaration regardless of which path ran.
"""

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.types import NotBlankStr


def expected_artifact_from_spec(spec: NotBlankStr) -> ExpectedArtifact:
    """Project a free-text expected-artifact spec onto a typed declaration.

    The type is inferred from the path so the dispatched task's fail-loud
    zero-artifact guard has a typed declaration to check against, defaulting to
    ``CODE``.

    Args:
        spec: The free-text expected-artifact spec (typically a file path or
            deliverable name); its lowercased form drives the type inference
            and it is carried through verbatim as the artifact path.

    Returns:
        An :class:`ExpectedArtifact` with an inferred type and the spec as its
        path.
    """
    lowered = spec.lower()
    if "test" in lowered:
        artifact_type = ArtifactType.TESTS
    elif lowered.endswith(".md") or "doc" in lowered:
        artifact_type = ArtifactType.DOCUMENTATION
    else:
        artifact_type = ArtifactType.CODE
    return ExpectedArtifact(type=artifact_type, path=spec)
