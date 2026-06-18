"""Rebind an ``asyncio.Lock`` to the currently running event loop.

A long-lived service that outlives the loop it was built on (the common
case under ``pytest-asyncio``, which creates a fresh loop per test) holds
a lock bound to a now-closed loop; awaiting it raises "loop is closed".
This helper returns a lock bound to the running loop, building a fresh
one when the recorded loop is stale, so the owner can store the result
back on its ``(_lock, _lock_loop)`` attribute pair.
"""

import asyncio


def rebind_lock_for_loop(
    lock: asyncio.Lock | None,
    recorded_loop: asyncio.AbstractEventLoop | None,
    *,
    preserve_preseeded: bool = False,
) -> tuple[asyncio.Lock, asyncio.AbstractEventLoop | None]:
    """Return a lock bound to the running loop, rebinding when stale.

    Args:
        lock: The currently held lock, or ``None`` if not built yet.
        recorded_loop: The loop the lock was last bound to, or ``None``
            when it has never been bound (e.g. a test-injected lock).
        preserve_preseeded: When ``True``, a ``None`` *recorded_loop*
            leaves *lock* untouched -- used for a lock a race test may
            have pre-acquired before any loop ran. When ``False``, a
            ``None`` recorded loop is treated as stale and the lock is
            rebound to the running loop.

    Returns:
        A ``(lock, recorded_loop)`` pair to store back on the owner. When
        no loop is running and a lock exists, both are returned
        unchanged; the caller awaits the lock once a loop is up.
    """
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        if lock is None:
            return asyncio.Lock(), recorded_loop
        return lock, recorded_loop
    if preserve_preseeded:
        stale = recorded_loop is not None and recorded_loop is not current
    else:
        stale = recorded_loop is not current
    if lock is None or stale:
        return asyncio.Lock(), current
    return lock, recorded_loop
