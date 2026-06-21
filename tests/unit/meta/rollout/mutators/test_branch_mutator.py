"""BranchRevertMutator deletes the remote branch via the GitHub client."""

from unittest.mock import AsyncMock

import pytest

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.protocol import GitHubAPI
from synthorg.meta.rollout.mutators import BranchRevertMutator
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestBranchRevertMutator:
    async def test_delete_branch_calls_client(self) -> None:
        client = mock_of[GitHubAPI](delete_branch=AsyncMock())
        mutator = BranchRevertMutator(github_client=client)

        await mutator.delete_branch(name="meta/code-mod/abc12345")

        client.delete_branch.assert_awaited_once_with("meta/code-mod/abc12345")

    async def test_blank_name_rejected(self) -> None:
        client = mock_of[GitHubAPI](delete_branch=AsyncMock())
        mutator = BranchRevertMutator(github_client=client)

        with pytest.raises(RollbackMutationDeniedError, match="non-blank"):
            await mutator.delete_branch(name="   ")
        client.delete_branch.assert_not_awaited()

    async def test_client_failure_surfaces_as_denied(self) -> None:
        client = mock_of[GitHubAPI](
            delete_branch=AsyncMock(side_effect=RuntimeError("403 forbidden"))
        )
        mutator = BranchRevertMutator(github_client=client)

        with pytest.raises(RollbackMutationDeniedError, match="branch delete failed"):
            await mutator.delete_branch(name="meta/code-mod/abc12345")
