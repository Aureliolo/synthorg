# module-kind: code
"""How the workspace held a run's declared artifacts before it started.

The declared-artifact check runs after a run finishes, and on its own it can
only ask whether the declared paths are there. For a task that edits a file
that already exists, which is most engineering work, they were there before
the agent did anything, so the answer says nothing about this run.

The baseline is the same probe asked when the run begins, and it rides a
:class:`~contextvars.ContextVar` for the same reason the resume flag does:
the two ends are far apart, and threading a value through every ``execute()``
and post-execution signature between them would put a parameter about one
invariant into every function on the path.

An unset baseline is not a verdict. A run whose engine wired no baseliner
falls back to the presence question, because missing evidence must never
fail a run that delivered.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

from synthorg.core.artifact import ExpectedArtifact
from synthorg.engine.artifacts.expected_artifact_check import (
    ArtifactPresence,
    ExpectedArtifactProbe,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)

logger = get_logger(__name__)

_BASELINE: ContextVar[ArtifactPresence | None] = ContextVar(
    "synthorg_artifact_baseline", default=None
)


@contextmanager
def artifact_baseline_scope(baseline: ArtifactPresence | None) -> Iterator[None]:
    """Publish *baseline* as how the enclosed run found its declarations.

    Args:
        baseline: The pre-run digests, or ``None`` when none was captured.

    Yields:
        None. Within the block, :func:`current_artifact_baseline` returns
        *baseline*.
    """
    token = _BASELINE.set(baseline)
    try:
        yield
    finally:
        _BASELINE.reset(token)


def current_artifact_baseline() -> ArtifactPresence | None:
    """Return the running task's pre-run artifact digests.

    Returns:
        The baseline set by :func:`artifact_baseline_scope`, or ``None``
        outside one.
    """
    return _BASELINE.get()


async def capture_run_baseline(
    probe: ExpectedArtifactProbe | None,
    *,
    project_id: str,
    expected: Sequence[ExpectedArtifact],
) -> ArtifactPresence | None:
    """Ask *probe* how the workspace holds *expected* right now.

    Args:
        probe: The wired declared-artifact probe, or ``None``.
        project_id: The project whose workspace holds the declarations.
        expected: What the task about to run declared it would produce.

    Returns:
        What the workspace said, or ``None`` when there is nothing to compare
        against later. Three conditions produce that ``None`` and they are not
        the same thing: a task that declared nothing has no baseline to want,
        while an unwired probe and an unreadable workspace each leave a run
        that did declare something judged on presence alone. Only the last two
        are reported, and each names itself. An unread baseline degrades the
        post-execution check to presence, which must never fail a run that
        delivered.

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
        # lint-allow: swallow-ok -- an unread baseline degrades the
        # declared-artifact check to presence, which is what shipped before
        # the baseline existed.
        logger.error(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            project_id=project_id,
            phase="baseline",
            reason="workspace unreadable",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


__all__ = [
    "artifact_baseline_scope",
    "capture_run_baseline",
    "current_artifact_baseline",
]
