"""Unit tests for :class:`OAuthStateService`.

The service is the audit-aware facade ``OAuthController.initiate_flow``
routes its single ``persistence.oauth_states.save(...)`` write
through.  These tests pin the delegate-and-bind contract:

- ``persist_initiation`` calls the repo's ``save`` exactly once.
- The persisted state has its ``connection_name`` bound to the value
  the caller supplied (the wire-level intent of the flow).
- The returned state instance reflects the same binding.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import OAuthState
from synthorg.integrations.oauth.state_service import OAuthStateService
from synthorg.persistence.connection_protocol import OAuthStateRepository

pytestmark = pytest.mark.unit


def _state(connection_name: str = "pending") -> OAuthState:
    """Build a minimal valid OAuthState for the tests."""
    return OAuthState(
        state_token=NotBlankStr("tok-abcdef-12345"),
        connection_name=NotBlankStr(connection_name),
        pkce_verifier=NotBlankStr("c" * 64),
        scopes_requested="read",
        redirect_uri="https://example.invalid/cb",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


class TestPersistInitiation:
    async def test_binds_connection_name_and_persists(self) -> None:
        repo = AsyncMock(spec=OAuthStateRepository)
        service = OAuthStateService(repo=repo)
        # Hold the actual input instance the service receives so the
        # immutability assertion below verifies *that* object stayed
        # untouched -- a freshly-constructed sibling would still have
        # ``"placeholder"`` regardless of whether the service mutated
        # the original.
        original = _state(connection_name="placeholder")
        bound = await service.persist_initiation(
            original,
            connection_name=NotBlankStr("github-prod"),
        )
        assert bound.connection_name == "github-prod"
        repo.save.assert_awaited_once()
        saved = repo.save.await_args.args[0]
        assert saved.connection_name == "github-prod"
        # Frozen Pydantic: the actual passed-in instance is untouched.
        assert original.connection_name == "placeholder"

    async def test_overrides_existing_connection_name(self) -> None:
        """Re-binding mid-flow swaps the name -- last write wins."""
        repo = AsyncMock(spec=OAuthStateRepository)
        service = OAuthStateService(repo=repo)
        bound = await service.persist_initiation(
            _state(connection_name="stale"),
            connection_name=NotBlankStr("github-prod"),
        )
        assert bound.connection_name == "github-prod"
