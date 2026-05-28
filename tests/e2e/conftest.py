"""End-to-end test configuration and fixtures.

The scripted provider and the identity / task / response builders are
the canonical shared implementation; this conftest only keeps the
import path stable and owns the workspace fixture.
"""

import asyncio
import sys
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

import pytest

from tests._shared.scripted_provider import (
    ScriptedProvider,
    make_e2e_identity,
    make_e2e_task,
    make_text_response,
    make_tool_call_response,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "ScriptedProvider",
    "make_e2e_identity",
    "make_e2e_task",
    "make_text_response",
    "make_tool_call_response",
]


# E2E tests drive real subprocesses (``asyncio.create_subprocess_exec``
# via the embedded git backend seed), which the Windows
# ``SelectorEventLoop`` cannot run (no IOCP integration means
# ``CreateProcessW`` cannot be wired into the loop). Pin
# ``ProactorEventLoop`` explicitly at the tier root via the
# ``pytest_asyncio_loop_factories`` hook so the selection is
# discovered through pytest's plugin manager rather than relying on a
# hook defined inside a test module (which pytest does not register
# unless the module is loaded as a plugin).
if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``ProactorEventLoop`` for the e2e tier on Windows."""
        return {"proactor": asyncio.ProactorEventLoop}


@pytest.fixture
def e2e_workspace(tmp_path: Path) -> Path:
    """Isolated temporary directory for real file tool operations."""
    workspace = tmp_path / "agent_workspace"
    workspace.mkdir()
    return workspace
