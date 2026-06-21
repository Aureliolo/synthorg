"""PrincipleOverridePromptMutator routes through the override repository."""

from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.rollout.mutators import PrincipleOverridePromptMutator
from synthorg.persistence.principle_override_protocol import (
    PrincipleOverrideRepository,
)

pytestmark = pytest.mark.unit


def _make_repo() -> tuple[PrincipleOverrideRepository, AsyncMock]:
    repo = create_autospec(PrincipleOverrideRepository, instance=True, spec_set=True)
    save_mock = AsyncMock()
    repo.save = save_mock
    return cast("PrincipleOverrideRepository", repo), save_mock


class TestPrincipleOverridePromptMutator:
    async def test_restore_persists_override(self) -> None:
        repo, save_mock = _make_repo()
        mutator = PrincipleOverridePromptMutator(override_repo=repo)

        await mutator.restore_principle(
            scope="planning.scope.alpha",
            text="Restored principle text",
        )

        save_mock.assert_awaited_once()
        args = save_mock.call_args.args
        entity = args[0]
        assert str(entity.scope) == "planning.scope.alpha"
        assert str(entity.text) == "Restored principle text"
        assert str(entity.restored_from) == "rollback"

    async def test_restore_persists_operation_id_provenance(self) -> None:
        """``operation_id`` is woven into ``restored_from`` for forensic audit."""
        repo, save_mock = _make_repo()
        mutator = PrincipleOverridePromptMutator(override_repo=repo)

        await mutator.restore_principle(
            scope="planning.scope.alpha",
            text="Restored principle text",
            operation_id="rb-2026-05-14-abc",
        )

        args = save_mock.call_args.args
        entity = args[0]
        assert str(entity.restored_from) == "rollback:rb-2026-05-14-abc"

    async def test_blank_scope_rejected(self) -> None:
        repo, save_mock = _make_repo()
        mutator = PrincipleOverridePromptMutator(override_repo=repo)

        with pytest.raises(RollbackMutationDeniedError, match="non-blank"):
            await mutator.restore_principle(scope="   ", text="text")
        save_mock.assert_not_awaited()

    async def test_blank_text_rejected(self) -> None:
        repo, save_mock = _make_repo()
        mutator = PrincipleOverridePromptMutator(override_repo=repo)

        with pytest.raises(RollbackMutationDeniedError, match="non-blank"):
            await mutator.restore_principle(scope="planning.scope", text="")
        save_mock.assert_not_awaited()

    async def test_repo_failure_surfaces_as_denied(self) -> None:
        repo, save_mock = _make_repo()
        save_mock.side_effect = RuntimeError("disk full")
        mutator = PrincipleOverridePromptMutator(override_repo=repo)

        with pytest.raises(
            RollbackMutationDeniedError,
            match="override save failed",
        ):
            await mutator.restore_principle(
                scope="planning.scope",
                text="text",
            )

    async def test_refresh_hook_invoked_after_successful_save(self) -> None:
        repo, save_mock = _make_repo()
        calls: list[int] = []

        async def _hook() -> None:
            calls.append(1)

        mutator = PrincipleOverridePromptMutator(
            override_repo=repo, on_override_written=_hook
        )
        await mutator.restore_principle(scope="planning.scope", text="text")

        save_mock.assert_awaited_once()
        assert calls == [1]

    async def test_refresh_hook_failure_does_not_fail_restore(self) -> None:
        repo, _save_mock = _make_repo()

        async def _hook() -> None:
            msg = "refresh boom"
            raise RuntimeError(msg)

        mutator = PrincipleOverridePromptMutator(
            override_repo=repo, on_override_written=_hook
        )
        # The durable write already succeeded, so a refresh failure is
        # swallowed: restore_principle must not raise.
        await mutator.restore_principle(scope="planning.scope", text="text")

    async def test_refresh_hook_not_invoked_when_save_fails(self) -> None:
        repo, save_mock = _make_repo()
        save_mock.side_effect = RuntimeError("disk full")
        calls: list[int] = []

        async def _hook() -> None:
            calls.append(1)

        mutator = PrincipleOverridePromptMutator(
            override_repo=repo, on_override_written=_hook
        )
        with pytest.raises(RollbackMutationDeniedError):
            await mutator.restore_principle(scope="planning.scope", text="text")
        assert calls == []
