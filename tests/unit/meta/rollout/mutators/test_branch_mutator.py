"""BranchRevertMutator deletes the remote branch via the GitHub client."""

from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.protocol import GitHubAPI
from synthorg.meta.rollout.mutators import BranchRevertMutator

pytestmark = pytest.mark.unit


def _make_client() -> tuple[GitHubAPI, AsyncMock]:
    client = create_autospec(GitHubAPI, instance=True, spec_set=True)
    delete_mock = AsyncMock()
    client.delete_branch = delete_mock
    return cast("GitHubAPI", client), delete_mock


class TestBranchRevertMutator:
    async def test_delete_branch_calls_client(self) -> None:
        client, delete_mock = _make_client()
        mutator = BranchRevertMutator(github_client=client)

        await mutator.delete_branch(name="meta/code-mod/abc12345")

        delete_mock.assert_awaited_once_with("meta/code-mod/abc12345")

    async def test_blank_name_rejected(self) -> None:
        client, delete_mock = _make_client()
        mutator = BranchRevertMutator(github_client=client)

        with pytest.raises(RollbackMutationDeniedError, match="non-blank"):
            await mutator.delete_branch(name="   ")
        delete_mock.assert_not_awaited()

    async def test_client_failure_surfaces_as_denied(self) -> None:
        client, delete_mock = _make_client()
        delete_mock.side_effect = RuntimeError("403 forbidden")
        mutator = BranchRevertMutator(github_client=client)

        with pytest.raises(RollbackMutationDeniedError, match="branch delete failed"):
            await mutator.delete_branch(name="meta/code-mod/abc12345")
