"""Widen mypy's parallel-worker IPC timeouts before mypy binds them.

mypy's parallel build (``--num-workers``) spawns ``mypy.build_worker``
subprocesses that connect back to the parent. The worker's server waits for the
parent only for ``WORKER_CONNECTION_TIMEOUT`` and the parent polls the worker's
status for ``WORKER_START_TIMEOUT``; several fresh interpreters importing the
compiled mypy package do not reliably win that window under the pre-push's
process contention, and when it lapses the connection drops and mypy aborts with
an INTERNAL ERROR instead of a type result. These two values are hardcoded
``Final`` constants in ``mypy/defaults.py`` with no environment or command-line
override, so they are widened here as ceilings on how long each side waits (the
happy path, an immediate connection, is unaffected).

This only bites when workers actually exist. ``scripts/run_affected_mypy.py``
runs mypy single-process on Windows (``--num-workers`` omitted), because the
Windows named-pipe worker transport is where the connection race manifested as
``WinError 233`` (ERROR_PIPE_NOT_CONNECTED); with no workers there, this hook is
inert on Windows. It stays effective for the POSIX socketpair workers that
``run_affected_mypy.py`` still spawns, widening their startup ceilings under
contention.

``run_affected_mypy.py`` puts this directory on ``PYTHONPATH`` for its mypy
subprocesses; ``site`` imports ``sitecustomize`` at interpreter startup in the
parent and in every worker (workers inherit ``PYTHONPATH`` via ``os.environ``)
before mypy is first imported, and it is inert for any interpreter that never
imports mypy.
"""

from typing import Final

_WORKER_IPC_TIMEOUT_SECONDS: Final[int] = 60

try:
    import mypy.defaults as _mypy_defaults
except ImportError:
    pass
else:
    # These are ``Final`` in mypy/defaults.py; overriding them at startup is the
    # whole purpose of this hook, so the reassignment is intentional.
    _mypy_defaults.WORKER_CONNECTION_TIMEOUT = _WORKER_IPC_TIMEOUT_SECONDS  # type: ignore[misc]
    _mypy_defaults.WORKER_START_TIMEOUT = _WORKER_IPC_TIMEOUT_SECONDS  # type: ignore[misc]
