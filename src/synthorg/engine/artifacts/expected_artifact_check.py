# module-kind: code
"""Did the task produce the artifacts it declared?

A task that declares ``artifacts_expected`` is promising deliverables. A
zero-tool-call count is only a proxy for that promise: an agent that read two
files, wrote nothing and stopped made tool calls, so the proxy alone waves it
through to review as if it had delivered. This asks the question the proxy
stands in for, against the project's own workspace.

A declaration is free text. The planner may name a file (``src/game.py``) or
a deliverable (``the integrated, runnable deliverable``), and
``ExpectedArtifact.path`` carries whichever it wrote. Only a declaration
shaped like a path is probed: prose resolves to no file, so probing it would
read as "produced nothing" and fail every task whose planner wrote a
sentence, the integration task included. Prose is left to the reviewer, which
can read it.

An absolute declaration is never probed either. Containment is what makes the
answer about the task's own output, and a path the run could not have written
under its own workspace is not evidence of delivery.

The verdict is deliberately "none of the probed paths present", not "all of
them": an agent that legitimately put one file somewhere else should reach
review and let the completion oracle judge the substitution, while an agent
that delivered nothing at all is the case the invariant exists for.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.artifact import ExpectedArtifact
from synthorg.engine.workspace.paths import project_workspace_dir


class ArtifactPresence(BaseModel):
    """What the workspace says about a task's declared artifacts.

    Attributes:
        probed: The declarations path-shaped enough to ask about.
        missing: Those of *probed* with nothing at them.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    probed: tuple[str, ...] = Field(
        default=(),
        description="Declarations that were probeable as filesystem paths",
    )
    missing: tuple[str, ...] = Field(
        default=(),
        description="Probed declarations with nothing at them",
    )

    @property
    def nothing_delivered(self) -> bool:
        """Did every probeable declaration come back absent?

        Kept here rather than derived by each caller: comparing two lengths
        at a call site is the kind of arithmetic that silently inverts, and
        the "none, not some" rule is this module's to state once.

        Returns:
            ``True`` when at least one declaration was probeable and none of
            them was found. A task whose declarations were all prose returns
            ``False``: nothing was asked, so nothing was answered.
        """
        return bool(self.probed) and len(self.missing) == len(self.probed)


#: Resolves ``(project_id, expected) -> what the workspace says``. The engine
#: holds one of these rather than a workspace root, so the layout knowledge
#: stays in the wiring layer that already owns it. Async because the answer
#: comes from the filesystem, and every consumer sits on the event loop.
type ExpectedArtifactProbe = Callable[
    [str, Sequence[ExpectedArtifact]], Awaitable[ArtifactPresence]
]


def is_probeable_path(spec: str) -> bool:
    """Can *spec* be honestly asked about as a filesystem path?

    Args:
        spec: A declaration exactly as the planner wrote it.

    Returns:
        ``True`` for a relative, whitespace-free declaration. Prose, and any
        absolute path, return ``False``.

        Whitespace is the discriminator rather than punctuation: ``dist``,
        ``README`` and ``Makefile`` are real deliverables carrying neither a
        separator nor a suffix, while a deliverable a planner describes
        rather than names is a phrase.
    """
    candidate = spec.strip()
    if not candidate or any(character.isspace() for character in candidate):
        return False
    return not (
        PurePosixPath(candidate).is_absolute()
        or PureWindowsPath(candidate).is_absolute()
    )


def missing_expected_artifacts(
    expected: Sequence[ExpectedArtifact],
    *,
    workspace: Path,
) -> ArtifactPresence:
    """Ask *workspace* which of the declared paths are there.

    Args:
        expected: The artifacts the task declared it would produce.
        workspace: The project's workspace directory. It need not exist:
            an unprovisioned workspace means nothing was produced.

    Returns:
        The probeable declarations and which of them are absent.
    """
    root = workspace.resolve()
    probed: list[str] = []
    missing: list[str] = []
    for artifact in expected:
        declared = str(artifact.path)
        if not is_probeable_path(declared):
            continue
        probed.append(declared)
        resolved = (root / Path(declared)).resolve()
        # ``resolved == root`` is a declaration of the workspace itself
        # (``.``, ``src/..``). It exists whenever the directory does, so
        # counting it as produced would let any run declare the workspace
        # and satisfy the check without writing a byte.
        if (
            resolved == root
            or not resolved.is_relative_to(root)
            or not resolved.exists()
        ):
            missing.append(declared)
    return ArtifactPresence(probed=tuple(probed), missing=tuple(missing))


def workspace_artifact_probe(base_root: Path) -> ExpectedArtifactProbe:
    """Bind an :data:`ExpectedArtifactProbe` to the shared workspace root.

    Args:
        base_root: Root every project's workspace lives under.

    Returns:
        A probe resolving each project's own workspace directory.
    """

    async def _probe(
        project_id: str, expected: Sequence[ExpectedArtifact]
    ) -> ArtifactPresence:
        """Probe *project_id*'s workspace for *expected*.

        Returns:
            The probeable declarations and which of them are absent.
        """
        return await asyncio.to_thread(
            missing_expected_artifacts,
            expected,
            workspace=project_workspace_dir(base_root, project_id),
        )

    return _probe


__all__ = [
    "ArtifactPresence",
    "ExpectedArtifactProbe",
    "is_probeable_path",
    "missing_expected_artifacts",
    "workspace_artifact_probe",
]
