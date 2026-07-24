"""Tests for the forge agent result-model invariants (identifier safety)."""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeAccessibleRepo,
)

pytestmark = pytest.mark.unit


class TestForgeAccessibleRepo:
    def test_plain_identifiers_accepted(self) -> None:
        repo = ForgeAccessibleRepo(
            owner=NotBlankStr("acme"),
            repo=NotBlankStr("proj-1"),
            permission="admin",
        )
        assert str(repo.owner) == "acme"

    @pytest.mark.parametrize(
        ("owner", "repo"),
        [
            ("acme/evil", "proj"),
            ("acme", "proj/../secret"),
            ("acme", "pr%2e"),
            ("ac#me", "proj"),
        ],
        ids=["owner_slash", "repo_traversal", "repo_percent", "owner_hash"],
    )
    def test_unsafe_forge_returned_identifier_rejected(
        self, owner: str, repo: str
    ) -> None:
        # A forge response is untrusted here: an embedded separator /
        # traversal / control character would flow into the scope string
        # and a URL path segment, so it is rejected on the read side too.
        with pytest.raises(ValidationError):
            ForgeAccessibleRepo(
                owner=NotBlankStr(owner),
                repo=NotBlankStr(repo),
                permission="read",
            )
