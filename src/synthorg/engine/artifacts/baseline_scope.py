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

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from synthorg.engine.artifacts.expected_artifact_check import ArtifactPresence

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


__all__ = ["artifact_baseline_scope", "current_artifact_baseline"]
