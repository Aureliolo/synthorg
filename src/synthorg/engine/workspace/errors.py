"""Workspace push-queue coordinator lifecycle errors.

The push-queue coordinator owns the FIFO single-writer position for git
pushes; its restart invariant is distinct from the general workspace
setup / merge / cleanup taxonomy in :mod:`synthorg.engine.errors`, so its
lifecycle error lives beside the coordinator it guards.
"""

from synthorg.engine.errors import WorkspaceError


class PushQueueUnrestartableError(WorkspaceError):
    """Raised when ``start()`` is called after a ``stop()`` drain timed out.

    A drain timeout means a worker (typically a hung git push) was cancelled
    without confirming it released the FIFO single-writer position, so the
    coordinator stays down rather than risk a second concurrent worker.
    """


__all__ = ["PushQueueUnrestartableError"]
