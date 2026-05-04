"""Unit test configuration and fixtures."""

import asyncio
import sys
import warnings
from typing import Any

import pytest


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """Use ``WindowsSelectorEventLoopPolicy`` on Windows for unit tests.

    Avoids a Python 3.14 race in ``ProactorEventLoop``'s IOCP teardown
    (https://github.com/python/cpython/issues/116773 and family) where
    a pending ``OVERLAPPED`` write can land on memory that the loop
    has already freed during ``CloseHandle(_iocp)``, segfaulting the
    worker process.  pytest-asyncio defaults to function-scoped event
    loops, so each async test creates and tears down a fresh
    ProactorEventLoop; the isolation gate's ``--count 2`` replay
    doubles the teardowns and concurrent worktrees amplify the race
    window further.

    SelectorEventLoop uses ``select`` rather than IOCP and is not
    subject to this race.  No unit test depends on subprocess on the
    active loop (every ``create_subprocess_exec`` reference under
    ``tests/unit/`` is a ``unittest.mock.patch`` target), so the
    selector-only feature gap does not regress any fixture.

    Integration and conformance tiers already opt in via their own
    ``event_loop_policy`` overrides in
    ``tests/integration/persistence/conftest.py`` and
    ``tests/conformance/persistence/conftest.py``.

    ``tests/unit/tools/conftest.py`` shadows this fixture for tool
    tests that must keep ``ProactorEventLoopPolicy`` (they drive real
    ``asyncio.create_subprocess_exec`` which the selector loop cannot).
    pytest's nested-conftest fixture resolution gives each test the
    closest definition, so the override applies cleanly per directory.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if sys.platform == "win32":
            return asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore]
        return asyncio.DefaultEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore,unreachable]
