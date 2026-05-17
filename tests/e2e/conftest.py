"""End-to-end test configuration and fixtures.

The scripted provider and the identity / task / response builders are
the canonical shared implementation; this conftest only keeps the
import path stable and owns the workspace fixture.
"""

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


@pytest.fixture
def e2e_workspace(tmp_path: Path) -> Path:
    """Isolated temporary directory for real file tool operations."""
    workspace = tmp_path / "agent_workspace"
    workspace.mkdir()
    return workspace
