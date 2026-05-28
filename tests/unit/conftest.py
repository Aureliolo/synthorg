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
# (``SelectorEventLoop`` on Windows cannot drive subprocess; it has no
# IOCP integration, so ``CreateProcessW`` cannot be wired into the
# event loop). pytest registers conftest hooks in path order (root
# first, progressively deeper); pluggy invokes ``firstresult=True``
# hooks in REVERSE registration order, so the deeper conftest's hook
# fires first and its non-None result wins.

if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``SelectorEventLoop`` on Windows for unit tests."""
        return {"selector": asyncio.SelectorEventLoop}
