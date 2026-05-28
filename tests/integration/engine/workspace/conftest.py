"""Integration-test fixtures for the project workspace acceptance suite."""

import asyncio
import sys
from collections.abc import Callable, Mapping

import pytest

# The acceptance suite drives ``git`` via ``asyncio.create_subprocess_exec``
# (through ``EmbeddedGitBackend``) and direct ``subprocess.run`` for worktree
# setup. ``SelectorEventLoop`` on Windows cannot drive ``create_subprocess_exec``;
# restore ``ProactorEventLoop`` here so the subprocess calls work.

if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``ProactorEventLoop`` for subprocess-driving workspace tests."""
        return {"proactor": asyncio.ProactorEventLoop}
