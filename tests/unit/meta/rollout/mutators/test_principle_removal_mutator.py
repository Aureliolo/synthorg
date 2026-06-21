"""ActivePrincipleRemovalMutator deletes through the active-principle store."""

from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.rollout.mutators import ActivePrincipleRemovalMutator
from synthorg.persistence.active_principle_protocol import ActivePrincipleRepository

pytestmark = pytest.mark.unit


def _make_repo() -> tuple[ActivePrincipleRepository, AsyncMock]:
    repo = create_autospec(ActivePrincipleRepository, instance=True, spec_set=True)
    delete_mock = AsyncMock(return_value=True)
    repo.delete = delete_mock
    return cast("ActivePrincipleRepository", repo), delete_mock


class TestActivePrincipleRemovalMutator:
    async def test_remove_deletes_by_id(self) -> None:
        repo, delete_mock = _make_repo()
        mutator = ActivePrincipleRemovalMutator(repo=repo)

        await mutator.remove(principle_id="3f2504e0-4f89-41d3-9a0c-0305e82c3301")

        delete_mock.assert_awaited_once()
        assert str(delete_mock.call_args.args[0]) == (
            "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
        )

    async def test_blank_id_rejected(self) -> None:
        repo, delete_mock = _make_repo()
        mutator = ActivePrincipleRemovalMutator(repo=repo)

        with pytest.raises(RollbackMutationDeniedError, match="non-blank"):
            await mutator.remove(principle_id="  ")
        delete_mock.assert_not_awaited()

    async def test_repo_failure_surfaces_as_denied(self) -> None:
        repo, delete_mock = _make_repo()
        delete_mock.side_effect = RuntimeError("locked")
        mutator = ActivePrincipleRemovalMutator(repo=repo)

        with pytest.raises(RollbackMutationDeniedError, match="delete failed"):
            await mutator.remove(principle_id="abc")

    async def test_refresh_hook_invoked_after_delete(self) -> None:
        repo, _delete_mock = _make_repo()
        calls: list[int] = []

        async def _hook() -> None:
            calls.append(1)

        mutator = ActivePrincipleRemovalMutator(repo=repo, on_principle_removed=_hook)
        await mutator.remove(principle_id="abc")

        assert calls == [1]

    async def test_refresh_hook_failure_does_not_fail_remove(self) -> None:
        repo, _delete_mock = _make_repo()

        async def _hook() -> None:
            msg = "refresh boom"
            raise RuntimeError(msg)

        mutator = ActivePrincipleRemovalMutator(repo=repo, on_principle_removed=_hook)
        # The durable delete already succeeded; a refresh failure is swallowed.
        await mutator.remove(principle_id="abc")

    async def test_refresh_hook_not_invoked_when_delete_fails(self) -> None:
        repo, delete_mock = _make_repo()
        delete_mock.side_effect = RuntimeError("locked")
        calls: list[int] = []

        async def _hook() -> None:
            calls.append(1)

        mutator = ActivePrincipleRemovalMutator(repo=repo, on_principle_removed=_hook)
        with pytest.raises(RollbackMutationDeniedError):
            await mutator.remove(principle_id="abc")
        assert calls == []
