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
        args, kwargs = save_mock.call_args
        assert str(args[0]) == "planning.scope.alpha"
        assert str(args[1]) == "Restored principle text"
        assert str(kwargs["restored_from"]) == "rollback"

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
