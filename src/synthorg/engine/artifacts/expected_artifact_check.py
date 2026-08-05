# module-kind: code
"""Did the task produce the artifacts it declared?

A task that declares ``artifacts_expected`` is promising files at named
paths. Until now the only guard was a zero-tool-call proxy: an agent that
read two files, wrote nothing and stopped had made tool calls, so it was
not classified as a no-op and went on to review as if it had delivered.
This asks the question the proxy stands in for -- are the declared paths
actually there -- against the project's own workspace.

The verdict is deliberately "none of them present", not "all of them":
an agent that legitimately put one file somewhere else should reach review
and let the completion oracle judge the substitution, while an agent that
delivered nothing at all is the case the invariant exists for.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from synthorg.core.artifact import ExpectedArtifact
from synthorg.engine.workspace.paths import project_workspace_dir

#: Resolves ``(project_id, expected) -> the declared paths that are absent``.
#: The engine holds one of these rather than a workspace root, so the layout
#: knowledge stays in the wiring layer that already owns it.
type ExpectedArtifactProbe = Callable[
    [str, Sequence[ExpectedArtifact]], tuple[str, ...]
]


def missing_expected_artifacts(
    expected: Sequence[ExpectedArtifact],
    *,
    workspace: Path,
) -> tuple[str, ...]:
    """Return the declared paths that do not exist under *workspace*.

    A declared path is relative to the project workspace; an absolute one is
    taken as given, since a task may legitimately name a path outside it
    (a published image, a remote ref) and this check has no business
    rewriting what the planner declared. A path that escapes the workspace
    counts as absent rather than being probed, because a deliverable the
    task cannot legitimately have written is not evidence of delivery.

    Args:
        expected: The artifacts the task declared it would produce.
        workspace: The project's workspace directory. It need not exist:
            an unprovisioned workspace means nothing was produced.

    Returns:
        Every declared path with nothing at it, in declaration order.
    """
    root = workspace.resolve()
    missing: list[str] = []
    for artifact in expected:
        candidate = Path(artifact.path)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )
        if not candidate.is_absolute() and not resolved.is_relative_to(root):
            missing.append(str(artifact.path))
            continue
        if not resolved.exists():
            missing.append(str(artifact.path))
    return tuple(missing)


def workspace_artifact_probe(base_root: Path) -> ExpectedArtifactProbe:
    """Bind an :data:`ExpectedArtifactProbe` to the shared workspace root.

    Args:
        base_root: Root every project's workspace lives under.

    Returns:
        A probe resolving each project's own workspace directory.
    """

    def _probe(
        project_id: str, expected: Sequence[ExpectedArtifact]
    ) -> tuple[str, ...]:
        """Probe *project_id*'s workspace for *expected*.

        Returns:
            Every declared path with nothing at it.
        """
        return missing_expected_artifacts(
            expected, workspace=project_workspace_dir(base_root, project_id)
        )

    return _probe


__all__ = [
    "ExpectedArtifactProbe",
    "missing_expected_artifacts",
    "workspace_artifact_probe",
]
