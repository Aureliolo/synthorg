"""Unit-test fixtures for the git-backend subsystem."""

import asyncio
import sys
from collections.abc import Callable, Mapping

import pytest

# Shadows ``tests/unit/conftest.py::pytest_asyncio_loop_factories``:
# the unit-tier root pins Windows tests to ``SelectorEventLoop`` to
# dodge a Python 3.14 IOCP teardown race, but ``SelectorEventLoop`` on
# Windows cannot drive ``asyncio.create_subprocess_exec`` -- which the
# git backends call into via ``run_git_subprocess``. These tests do not
# use Litestar TestClient / asgi-lifespan, so they do not trigger the
# rapid event-loop-creation pattern that exposes the race. Mirrors
# ``tests/unit/tools/conftest.py``.

if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``ProactorEventLoop`` for git-backend subprocess tests."""
        return {"proactor": asyncio.ProactorEventLoop}
