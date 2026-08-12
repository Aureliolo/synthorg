"""Which project a sandboxed command is scoped to, and what an absence means.

The sandbox reads an absent project as the whole-workspace root, one directory
above what the agent wrote and alongside every other project's files, so a
command that cannot be scoped is refused rather than widened to all of them.
Two very different faults produce that absence, and an operator reading the
logs has to be able to tell them apart: nothing bound the identity on the
calling path, or a run reached the sandbox with no project at all.
"""

from collections.abc import Mapping, Sequence

import pytest
import structlog

from synthorg.core.execution_identity import (
    ExecutionIdentity,
    execution_identity_scope,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.workspace import WORKSPACE_SCOPE_UNRESOLVED
from synthorg.tools._workspace_scope import require_project_id
from synthorg.tools.sandbox.errors import SandboxProjectScopeUnresolvedError

pytestmark = pytest.mark.unit


def _unresolved(
    captured: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    """Return the scope-unresolved records among *captured*.

    Returns:
        Every captured record for the unresolved-scope event.
    """
    return [
        record for record in captured if record["event"] == WORKSPACE_SCOPE_UNRESOLVED
    ]


class TestRequireProjectId:
    def test_a_scoped_run_resolves_its_project_silently(self) -> None:
        identity = ExecutionIdentity(
            execution_id=NotBlankStr("run-1"),
            task_id=NotBlankStr("task-1"),
            project_id=NotBlankStr("proj-1"),
        )
        with (
            structlog.testing.capture_logs() as captured,
            execution_identity_scope(identity),
        ):
            assert require_project_id() == "proj-1"

        assert _unresolved(captured) == []

    def test_an_unbound_identity_names_itself_and_refuses(self) -> None:
        with (
            structlog.testing.capture_logs() as captured,
            pytest.raises(SandboxProjectScopeUnresolvedError, match="identity"),
        ):
            require_project_id()

        records = _unresolved(captured)
        assert len(records) == 1
        assert records[0]["reason"] == "no execution identity bound"

    def test_a_projectless_run_names_itself_and_refuses(self) -> None:
        # Distinct from the above: the identity is bound and correct, and the
        # run simply has no project, so the fix is elsewhere.
        identity = ExecutionIdentity(
            execution_id=NotBlankStr("run-1"), task_id=NotBlankStr("task-1")
        )
        with (
            structlog.testing.capture_logs() as captured,
            execution_identity_scope(identity),
            pytest.raises(SandboxProjectScopeUnresolvedError, match="no project"),
        ):
            require_project_id()

        records = _unresolved(captured)
        assert len(records) == 1
        assert records[0]["reason"] == "run declares no project"
        assert records[0]["task_id"] == "task-1"
