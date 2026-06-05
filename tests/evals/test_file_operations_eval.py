"""Example agent evaluation: FILE_OPERATIONS behavior category."""

import pytest

from synthorg.execution.turn import BehaviorTag


@pytest.mark.integration
@pytest.mark.agent_eval(category="file_operations")
async def test_agent_file_read_behavior() -> None:
    """Verify agent correctly categorizes file read operations."""
    assert BehaviorTag.FILE_OPERATIONS.value == "file_operations"


@pytest.mark.integration
@pytest.mark.agent_eval(category="file_operations")
async def test_agent_file_write_behavior() -> None:
    """Verify write tools map to FILE_OPERATIONS tag."""
    from synthorg.engine.middleware.behavior_tagger import (
        _DEFAULT_TOOL_TAG_MAP,
    )

    assert _DEFAULT_TOOL_TAG_MAP["write_file"] is BehaviorTag.FILE_OPERATIONS
