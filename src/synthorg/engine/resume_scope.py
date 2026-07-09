"""Resume/continuation scope for the fail-loud no-op invariant.

The empty-run fail-loud check (a work task that produced no artifacts,
proxied by zero tool calls, terminates ``NO_OP`` -> ``FAILED``) reasons
over a single ``execute()`` segment's turns. A run that resumed from an
approval park (or replayed prior events) starts that segment with a
fresh, empty turn list even though earlier segments already produced
work, so the per-segment proxy would wrongly fail a task that has real
output. This scope marks a continued run so the classifier and the
post-execution transition both skip the empty-run failure for it; a
genuinely empty continued run still completes to review rather than
being discarded as a silent no-op.

The flag rides a :class:`~contextvars.ContextVar`, so it propagates
through the awaited execution of one run without threading a parameter
through the shared ``_execute`` / post-execution signatures.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_RESUMED_RUN: ContextVar[bool] = ContextVar("synthorg_resumed_run", default=False)


@contextmanager
def resumed_run_scope() -> Iterator[None]:
    """Mark the enclosed run as a resume/continuation of prior work.

    Yields:
        None. Within the block, :func:`is_resumed_run` returns ``True``.
    """
    token = _RESUMED_RUN.set(True)
    try:
        yield
    finally:
        _RESUMED_RUN.reset(token)


def is_resumed_run() -> bool:
    """Return whether the current run resumed/continued prior work.

    Returns:
        ``True`` inside a :func:`resumed_run_scope`, else ``False``.
    """
    return _RESUMED_RUN.get()
