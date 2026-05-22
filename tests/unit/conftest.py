"""Unit test configuration and fixtures."""

import asyncio
import sys
import warnings
from typing import Any

import pytest

# ── Windows: make SelectorEventLoop the process-wide default ────────
#
# The ``event_loop_policy`` fixture below pins *async* unit tests to the
# Selector loop, but it does not govern loops created outside
# pytest-asyncio: a sync test calling ``asyncio.run``, a litestar
# ``TestClient`` anyio portal, or the xdist worker's own teardown all
# fall back to the interpreter default -- the ``ProactorEventLoop`` on
# Windows. That loop's Python 3.14 IOCP teardown race
# (https://github.com/python/cpython/issues/116773) intermittently
# segfaults the worker ("node down"), and the failure surfaces on any
# change that widens the affected-tests pre-push selection (e.g. editing
# ``api/app.py`` or a broadly-imported ``core`` module).
#
# Setting the global default to Selector at conftest import closes that
# gap for every non-fixture loop in the unit worker. Tool tests that
# genuinely need ``create_subprocess_exec`` still receive a
# ``ProactorEventLoop`` because pytest-asyncio builds their loop from
# the shadowing ``tests/unit/tools/conftest.py`` ``event_loop_policy``
# fixture (applied per async test), which overrides this default for the
# duration of those tests.
if sys.platform == "win32":  # pragma: no cover -- Windows-only branch
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy(),  # type: ignore[attr-defined,unused-ignore]
        )


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
