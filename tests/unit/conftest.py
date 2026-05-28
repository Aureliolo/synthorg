"""Unit test configuration and fixtures."""

import asyncio
import sys
from collections.abc import Callable, Mapping

import pytest

# Pin pytest-asyncio loops to ``SelectorEventLoop`` on Windows via the
# ``pytest_asyncio_loop_factories`` pluggy hook. Selected at hook level
# rather than via the deprecated ``asyncio.set_event_loop_policy`` API
# (Python 3.14 deprecated, 3.16 removal).
#
# ``tests/unit/tools/conftest.py`` and
# ``tests/unit/engine/workspace/git_backend/conftest.py`` shadow this
# hook for tests that drive real ``asyncio.create_subprocess_exec``
# (``SelectorEventLoop`` on Windows cannot drive subprocess); pluggy's
# reverse-order invocation under ``firstresult=True`` lets the deeper
# conftest win.

if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``SelectorEventLoop`` on Windows for unit tests."""
        return {"selector": asyncio.SelectorEventLoop}
