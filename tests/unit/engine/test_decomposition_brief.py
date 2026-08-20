"""The planning brief must not let a planner invent a foundation.

A live run planned a brand-new, empty project on the file inventory of a
different project the org had built weeks earlier: seven filenames recalled
from org memory, asserted in the plan's own assumptions as "existing code from
prior work is sound and builds the foundation", and every one of the ten items
scoped as integration rather than construction. The project had no workspace
directory at all. Recalled experience is precedent; it is never inventory.
"""

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.decomposition.agent_session_brief import planning_brief
from synthorg.engine.decomposition.models import DecompositionContext
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def _task() -> Task:
    return Task(
        id=as_uuid("obj-1"),
        title="Build a Tetris web game",
        description="A playable browser Tetris.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="tetris-web",
        created_by="ceo",
    )


class TestBriefRefusesAnInventedFoundation:
    def test_the_brief_forbids_assuming_prior_work_exists(self) -> None:
        brief = planning_brief(_task(), DecompositionContext(), ())

        assert "already exist" in brief

    def test_the_brief_says_recall_is_not_inventory(self) -> None:
        """The digest carries other projects' work; it must not read as ours."""
        brief = planning_brief(_task(), DecompositionContext(), ())

        assert "another project" in brief

    def test_an_empty_workspace_is_stated_rather_than_left_unsaid(self) -> None:
        context = DecompositionContext(workspace_summary="empty")

        brief = planning_brief(_task(), context, ())

        assert "empty" in brief

    def test_a_populated_workspace_is_listed(self) -> None:
        context = DecompositionContext(workspace_summary="server.js, index.html, test/")

        brief = planning_brief(_task(), context, ())

        assert "server.js" in brief
        assert "index.html" in brief

    def test_no_workspace_supplied_still_carries_the_rule(self) -> None:
        """Not every caller can resolve a workspace; the rule is unconditional."""
        brief = planning_brief(_task(), DecompositionContext(), ())

        assert "already exist" in brief
