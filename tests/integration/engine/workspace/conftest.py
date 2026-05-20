"""Integration-test fixtures for the project workspace acceptance suite."""

import asyncio
import warnings
from typing import Any

import pytest


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """Restore ``ProactorEventLoopPolicy`` for subprocess-driving tests.

    The acceptance suite drives ``git`` via ``asyncio.create_subprocess_exec``
    (through ``EmbeddedGitBackend``) and direct ``subprocess.run`` for
    worktree setup. SelectorEventLoop on Windows cannot drive
    ``create_subprocess_exec``; restore the default Proactor policy here.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return asyncio.DefaultEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore]
