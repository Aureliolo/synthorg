# module-kind: code
"""How the workspace looked before a run started.

The delivery checks run after a run finishes, and on their own they can only
ask what is there now. For a task that edits a file that already exists,
which is most engineering work, everything was there before the agent did
anything, so the answer says nothing about this run.

The baseline is the same question asked when the run begins. It carries two
views of one workspace, because "did this run keep its promise" and "did this
run do anything" are different questions and the second is the one a loop
needs while it still has turns left:

* ``declared`` is the digest of each declared artifact, which answers whether
  the promise was kept;
* ``tree`` is every file under the workspace, which answers whether anything
  happened at all, without consulting a plan written before the tree existed.

One probe answers both, so the two views cannot describe different moments.

It rides a :class:`~contextvars.ContextVar` for the same reason the resume
flag does: the two ends are far apart, and threading a value through every
``execute()`` and post-execution signature between them would put a parameter
about one invariant into every function on the path.

An unset baseline is not a verdict. A run whose engine wired no probe falls
back to the weaker evidence it has, because missing evidence must never fail
a run that delivered.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.artifact import ExpectedArtifact
from synthorg.engine.artifacts.expected_artifact_check import (
    ArtifactPresence,
    missing_expected_artifacts,
)
from synthorg.engine.artifacts.workspace_fingerprint import (
    WorkspaceFingerprint,
    fingerprint_tree,
)
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)

logger = get_logger(__name__)


class RunBaseline(BaseModel):
    """What the workspace held when a run began.

    Attributes:
        workspace: The project's workspace directory, so the tree question
            can be asked again later against the same root.
        declared: The declared artifacts as they were found.
        tree: Every file under the workspace as it was found.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    workspace: Path = Field(description="The project's workspace directory")
    declared: ArtifactPresence = Field(
        description="How the workspace held the declared artifacts"
    )
    tree: WorkspaceFingerprint = Field(
        default=frozenset(), description="Every file under the workspace"
    )


#: Resolves ``(project_id, expected) -> how the workspace looks now``. The
#: engine holds one of these rather than a workspace root, so the layout
#: knowledge stays in the wiring layer that already owns it. Async because
#: the answer comes from the filesystem, and every consumer sits on the event
#: loop.
type RunBaselineProbe = Callable[
    [str, Sequence[ExpectedArtifact]], Awaitable[RunBaseline]
]

_BASELINE: ContextVar[RunBaseline | None] = ContextVar(
    "synthorg_run_baseline", default=None
)


def workspace_run_probe(base_root: Path) -> RunBaselineProbe:
    """Bind a :data:`RunBaselineProbe` to the shared workspace root.

    Args:
        base_root: Root every project's workspace lives under.

    Returns:
        A probe resolving each project's own workspace directory.
    """

    async def _probe(
        project_id: str, expected: Sequence[ExpectedArtifact]
    ) -> RunBaseline:
        """Ask *project_id*'s workspace about *expected* and about itself.

        Returns:
            Both views, taken from one directory in one call.
        """
        workspace = project_workspace_dir(base_root, project_id)
        return await asyncio.to_thread(_read, workspace, expected)

    return _probe


def _read(workspace: Path, expected: Sequence[ExpectedArtifact]) -> RunBaseline:
    """Read both views off *workspace*.

    Returns:
        The baseline. Runs on a worker thread; every consumer is async.
    """
    return RunBaseline(
        workspace=workspace,
        declared=missing_expected_artifacts(expected, workspace=workspace),
        tree=fingerprint_tree(workspace),
    )


@contextmanager
def run_baseline_scope(baseline: RunBaseline | None) -> Iterator[None]:
    """Publish *baseline* as how the enclosed run found its workspace.

    Args:
        baseline: What the workspace held before the run, or ``None`` when
            none was captured.

    Yields:
        None. Within the block, :func:`current_run_baseline` returns
        *baseline*.
    """
    token = _BASELINE.set(baseline)
    try:
        yield
    finally:
        _BASELINE.reset(token)


def current_run_baseline() -> RunBaseline | None:
    """Return how the running task found its workspace.

    Returns:
        The baseline set by :func:`run_baseline_scope`, or ``None`` outside
        one.
    """
    return _BASELINE.get()


async def capture_run_baseline(
    probe: RunBaselineProbe | None,
    *,
    project_id: str,
    expected: Sequence[ExpectedArtifact],
) -> RunBaseline | None:
    """Ask *probe* how the workspace looks right now.

    Args:
        probe: The wired workspace probe, or ``None``.
        project_id: The project whose workspace holds the declarations.
        expected: What the task about to run declared it would produce.

    Returns:
        What the workspace said, or ``None`` when there is nothing to compare
        against later. Three conditions produce that ``None`` and they are not
        the same thing: a task that declared nothing has no baseline to want,
        while an unwired probe and an unreadable workspace each leave a run
        that did declare something judged on weaker evidence. Only the last
        two are reported, and each names itself.

    Raises:
        Exception: Whatever a probe raises that is not storage I/O. The
            post-run half of this same probe catches ``OSError`` alone, and a
            programming error that silently disabled the baseline here while
            crashing the run there would be the same bug reported two
            incompatible ways.
    """
    if not expected:
        return None
    if probe is None:
        # Debug, not warning: this is a property of how the engine was built,
        # so it would repeat identically for every task of the process.
        logger.debug(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            project_id=project_id,
            phase="baseline",
            reason="no artifact probe wired",
        )
        return None
    try:
        return await probe(project_id, expected)
    except OSError as exc:
        # lint-allow: swallow-ok -- an unread baseline degrades the delivery
        # checks to the evidence that shipped before the baseline existed.
        logger.error(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            project_id=project_id,
            phase="baseline",
            reason="workspace unreadable",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def produced_nothing_since(baseline: RunBaseline | None) -> bool | None:
    """Has the workspace changed since *baseline* was taken?

    The single owner of "did this run do anything", asked by the in-session
    nudge, by the loop's no-op classification and by the post-run guard, so
    that the three cannot answer it differently.

    Args:
        baseline: How the workspace was found, or ``None`` when it was never
            asked.

    Returns:
        ``True`` when every file is exactly as the run found it, ``False``
        when something appeared, changed length or was removed, and ``None``
        when there is no baseline to compare against. ``None`` is not "the
        run produced nothing": it is the absence of evidence, and each caller
        falls back to what it had before.
    """
    if baseline is None:
        return None
    return (
        await asyncio.to_thread(fingerprint_tree, baseline.workspace)
    ) == baseline.tree


__all__ = [
    "RunBaseline",
    "RunBaselineProbe",
    "capture_run_baseline",
    "current_run_baseline",
    "produced_nothing_since",
    "run_baseline_scope",
    "workspace_run_probe",
]
