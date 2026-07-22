"""Widen mypy's parallel-worker IPC timeouts before mypy binds them.

mypy's parallel build (``--num-workers``) spawns ``mypy.build_worker``
subprocesses that connect back to the parent over a named pipe. The worker's
pipe server waits for the parent only for ``WORKER_CONNECTION_TIMEOUT`` and the
parent polls the worker's status file for ``WORKER_START_TIMEOUT`` (both 10s on
Windows). Several fresh interpreters importing the compiled mypy package do not
reliably win that window under the pre-push's process contention: when the
window lapses the worker closes the pipe and the parent's source-broadcast
``write_bytes`` dies with ``WinError 233`` (ERROR_PIPE_NOT_CONNECTED), aborting
mypy with an INTERNAL ERROR instead of a type result.

Those two values are hardcoded ``Final`` constants in ``mypy/defaults.py`` with
no environment or command-line override, so they are widened here. ``site``
imports ``sitecustomize`` at interpreter startup, which runs in the parent and
in every worker (workers inherit ``PYTHONPATH`` via ``os.environ``) before mypy
is first imported; a value set on ``mypy.defaults`` before ``mypy.build`` binds
it is honoured by the compiled build module. The values are ceilings on how long
each side waits, so the happy path (an immediate connection) is unaffected.

``scripts/run_affected_mypy.py`` puts this directory on ``PYTHONPATH`` for its
mypy subprocesses; it is inert for any interpreter that never imports mypy.
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
