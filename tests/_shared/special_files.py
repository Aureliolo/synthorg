"""Filesystem entries only POSIX can create, for the guards that refuse them.

A workspace is a tree an agent can write, so anything the OS lets it create
can end up in one. The code that walks a workspace has to survive that, and
the only way to pin it is to create the thing.
"""

import os
from pathlib import Path

import pytest


def make_named_pipe(path: Path) -> Path:
    """Create a named pipe at *path*, skipping where the platform has none.

    Reached through ``getattr`` rather than by name: ``os.mkfifo`` does not
    exist on Windows at all, so writing it out is a type error there, while a
    ``sys.platform`` guard instead makes every line after it unreachable,
    which is also one. This spelling type-checks on both and skips on the one
    that cannot honour it.

    Returns:
        The pipe created.
    """
    mkfifo = getattr(os, "mkfifo", None)
    if mkfifo is None:  # pragma: no cover - platform-dependent
        pytest.skip("named pipes are POSIX-only")
    mkfifo(path)
    return path


__all__ = ["make_named_pipe"]
