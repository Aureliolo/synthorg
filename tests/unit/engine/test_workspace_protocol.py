"""Tests for workspace isolation protocol."""

from typing import TYPE_CHECKING

import pytest

from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy

if TYPE_CHECKING:
    from synthorg.engine.workspace.models import (
        MergeResult,
        Workspace,
        WorkspaceRequest,
    )


class _ConformingStub:
    """Minimal stub implementing WorkspaceIsolationStrategy."""

    async def setup_workspace(
        self,
        *,
        request: WorkspaceRequest,
    ) -> Workspace:
        raise NotImplementedError

    async def teardown_workspace(
        self,
        *,
        workspace: Workspace,
    ) -> None:
        raise NotImplementedError

    async def merge_workspace(
        self,
        *,
        workspace: Workspace,
    ) -> MergeResult:
        raise NotImplementedError

    async def list_active_workspaces(self) -> tuple[Workspace, ...]:
        raise NotImplementedError

    def get_strategy_type(self) -> str:
        return "stub"


class TestWorkspaceIsolationStrategy:
    """Tests for WorkspaceIsolationStrategy protocol."""

    @pytest.mark.unit
    def test_protocol_is_runtime_checkable(self) -> None:
        """Conforming stub passes isinstance check."""
        assert isinstance(_ConformingStub(), WorkspaceIsolationStrategy)

    @pytest.mark.unit
    def test_non_conforming_class_rejected(self) -> None:
        """A class missing methods does not satisfy the protocol."""

        class NotAStrategy:
            pass

        assert not isinstance(NotAStrategy(), WorkspaceIsolationStrategy)

    @pytest.mark.unit
    def test_protocol_defines_expected_methods(self) -> None:
        """Protocol declares the expected method signatures."""
        expected = {
            "setup_workspace",
            "teardown_workspace",
            "merge_workspace",
            "list_active_workspaces",
            "get_strategy_type",
        }
        members = {
            name for name in dir(WorkspaceIsolationStrategy) if not name.startswith("_")
        }
        assert expected.issubset(members)
