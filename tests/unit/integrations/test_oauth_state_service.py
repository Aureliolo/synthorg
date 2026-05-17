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
            connection_name=NotBlankStr("example-provider-prod"),
        )
        assert bound.connection_name == "example-provider-prod"
        repo.save.assert_awaited_once()
        saved = repo.save.await_args.args[0]
        assert saved.connection_name == "example-provider-prod"
        # Frozen Pydantic: the actual passed-in instance is untouched.
        assert original.connection_name == "placeholder"

    async def test_overrides_existing_connection_name(self) -> None:
        """Re-binding mid-flow swaps the name -- last write wins."""
        repo = AsyncMock(spec=OAuthStateRepository)
        service = OAuthStateService(repo=repo)
        bound = await service.persist_initiation(
            _state(connection_name="stale"),
            connection_name=NotBlankStr("example-provider-prod"),
        )
        assert bound.connection_name == "example-provider-prod"

    async def test_repo_failure_propagates_after_audit_log(self) -> None:
        """Save errors propagate; the failure-event logging path runs first.

        ``OAuthStateService`` was extracted to centralise the
        ``persistence.oauth_states.save(...)`` write AND the
        ``SECURITY_OAUTH_STATE_PERSIST_FAILED`` audit log on the
        unhappy path.  A future refactor that drops the try/except
        around ``_repo.save`` would silently regress the audit
        contract; this test pins the propagation so that path is
        exercised end-to-end.
        """
        from synthorg.core.persistence_errors import PersistenceError

        repo = AsyncMock(spec=OAuthStateRepository)
        repo.save.side_effect = PersistenceError("backend down")
        service = OAuthStateService(repo=repo)
        with pytest.raises(PersistenceError, match="backend down"):
            await service.persist_initiation(
                _state(connection_name="placeholder"),
                connection_name=NotBlankStr("example-provider-prod"),
            )
        repo.save.assert_awaited_once()


class TestCallbackStateOps:
    """The callback path routes get / expire / mark_consumed through
    the service rather than touching ``persistence.oauth_states``
    directly, so the layer boundary holds on both flow halves.
    """

    async def test_get_delegates_to_repo(self) -> None:
        repo = AsyncMock(spec=OAuthStateRepository)
        state = _state(connection_name="example-provider-prod")
        repo.get.return_value = state
        service = OAuthStateService(repo=repo)

        result = await service.get(NotBlankStr("tok-abcdef-12345"))

        assert result is state
        repo.get.assert_awaited_once_with(NotBlankStr("tok-abcdef-12345"))

    async def test_get_missing_returns_none(self) -> None:
        repo = AsyncMock(spec=OAuthStateRepository)
        repo.get.return_value = None
        service = OAuthStateService(repo=repo)

        assert await service.get(NotBlankStr("missing-token-1")) is None

    async def test_expire_delegates_to_repo_delete(self) -> None:
        repo = AsyncMock(spec=OAuthStateRepository)
        repo.delete.return_value = True
        service = OAuthStateService(repo=repo)

        deleted = await service.expire(NotBlankStr("tok-abcdef-12345"))

        assert deleted is True
        repo.delete.assert_awaited_once_with(NotBlankStr("tok-abcdef-12345"))

    async def test_mark_consumed_delegates_with_kwargs(self) -> None:
        repo = AsyncMock(spec=OAuthStateRepository)
        repo.mark_consumed.return_value = True
        service = OAuthStateService(repo=repo)
        when = datetime.now(UTC)

        won = await service.mark_consumed(
            NotBlankStr("tok-abcdef-12345"),
            connection_name=NotBlankStr("example-provider-prod"),
            consumed_at=when,
        )

        assert won is True
        repo.mark_consumed.assert_awaited_once_with(
            NotBlankStr("tok-abcdef-12345"),
            connection_name=NotBlankStr("example-provider-prod"),
            consumed_at=when,
        )
