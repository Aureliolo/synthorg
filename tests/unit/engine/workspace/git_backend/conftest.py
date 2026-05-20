"""Unit-test fixtures for the git-backend subsystem."""

import asyncio
import warnings
from typing import Any

import pytest


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """Restore ``ProactorEventLoopPolicy`` for git-backend tests.

    Shadows ``tests/unit/conftest.py::event_loop_policy``: the unit-tier
    root pins Windows tests to ``SelectorEventLoopPolicy`` to dodge a
    Python 3.14 IOCP teardown race, but SelectorEventLoop on Windows
    cannot drive ``asyncio.create_subprocess_exec`` -- which the git
    backends call into via ``run_git_subprocess``. These tests do not
    use Litestar TestClient / asgi-lifespan, so they do not trigger the
    rapid event-loop-creation pattern that exposes the race. Mirrors
    ``tests/unit/tools/conftest.py``.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return asyncio.DefaultEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore]
