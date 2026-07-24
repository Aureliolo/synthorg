"""Tests for the connections request DTOs (repo-scope validation + sentinel)."""

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.connections_models import (
    CreateConnectionRequest,
    UpdateConnectionRequest,
)
from synthorg.integrations.connections.models import AuthMethod, ConnectionType

pytestmark = pytest.mark.unit


def _create(**overrides: object) -> CreateConnectionRequest:
    base: dict[str, object] = {
        "name": "forge",
        "connection_type": ConnectionType.GITHUB,
        "auth_method": AuthMethod.BEARER_TOKEN,
    }
    base.update(overrides)
    return CreateConnectionRequest(**base)  # type: ignore[arg-type]


class TestCreateAllowedRepos:
    def test_valid_scope_accepted(self) -> None:
        req = _create(allowed_repos=("acme/proj-1", "acme/*"))
        assert req.allowed_repos == ("acme/proj-1", "acme/*")

    @pytest.mark.parametrize("entry", ["*", "*/*", "acme", "ac*/repo"])
    def test_overbroad_entry_rejected(self, entry: str) -> None:
        with pytest.raises(ValidationError):
            _create(allowed_repos=(entry,))


class TestUpdateAllowedReposSentinel:
    def test_explicit_empty_clears_is_in_fields_set(self) -> None:
        # An explicit ``[]`` is the deny-all clear signal the controller
        # distinguishes from an omitted field via model_fields_set.
        req = UpdateConnectionRequest(allowed_repos=())
        assert "allowed_repos" in req.model_fields_set
        assert req.allowed_repos == ()

    def test_omitted_keeps_is_not_in_fields_set(self) -> None:
        req = UpdateConnectionRequest()
        assert "allowed_repos" not in req.model_fields_set
        assert req.allowed_repos is None

    def test_overbroad_entry_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UpdateConnectionRequest(allowed_repos=("*",))
