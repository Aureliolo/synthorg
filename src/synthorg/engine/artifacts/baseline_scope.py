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
from synthorg.core.critical_errors import reraise_critical
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
        against later: no probe wired, nothing declared, or a probe that
        could not answer. An unread baseline degrades the post-execution
        check to presence, which must never fail a run that delivered.
    """
    if probe is None or not expected:
        return None
    try:
        return await probe(project_id, expected)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- an unread baseline degrades the
        # declared-artifact check to presence, which is what shipped before
        # the baseline existed.
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            project_id=project_id,
            phase="baseline",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


__all__ = [
    "artifact_baseline_scope",
    "capture_run_baseline",
    "current_artifact_baseline",
]
