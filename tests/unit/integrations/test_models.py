"""Unit tests for integration domain models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.integrations.config import IntegrationsConfig
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
    OAuthState,
    SecretRef,
)


@pytest.mark.unit
class TestConnectionModel:
    """Tests for the Connection frozen model."""

    def test_default_construction(self) -> None:
        conn = Connection(
            name="test",
            connection_type=ConnectionType.GITHUB,
            auth_method=AuthMethod.BEARER_TOKEN,
        )
        assert conn.name == "test"
        assert conn.connection_type == ConnectionType.GITHUB
        assert conn.health.status == ConnectionStatus.UNKNOWN
        assert conn.secret_refs == ()
        assert conn.metadata == {}

    def test_frozen(self) -> None:
        conn = Connection(
            name="test",
            connection_type=ConnectionType.SLACK,
            auth_method=AuthMethod.OAUTH2,
        )
        with pytest.raises(ValidationError):
            conn.name = "changed"  # type: ignore[misc]

    def test_metadata_deep_copied(self) -> None:
        meta = {"key": "value"}
        conn = Connection(
            name="test",
            connection_type=ConnectionType.GITHUB,
            auth_method=AuthMethod.API_KEY,
            metadata=meta,
        )
        meta["key"] = "modified"
        assert conn.metadata["key"] == "value"

    def test_secret_refs_excluded_from_serialisation(self) -> None:
        # Audit 103: secret_refs hold backend-internal coordinates that
        # must not leak into API responses (they are serialised from
        # every Connection-returning endpoint).
        conn = Connection(
            name="test",
            connection_type=ConnectionType.SLACK,
            auth_method=AuthMethod.OAUTH2,
            secret_refs=(SecretRef(secret_id="abc-123", backend="encrypted_sqlite"),),
        )
        # The attribute is still readable (the repo persists it directly),
        # but model_dump / JSON serialisation drops it.
        assert len(conn.secret_refs) == 1
        assert "secret_refs" not in conn.model_dump()
        assert "secret_refs" not in conn.model_dump(mode="json")


@pytest.mark.unit
class TestSecretRefModel:
    """Tests for the SecretRef model."""

    def test_construction(self) -> None:
        ref = SecretRef(
            secret_id="abc-123",
            backend="encrypted_sqlite",
        )
        assert ref.secret_id == "abc-123"
        assert ref.key_version == 1


@pytest.mark.unit
class TestIntegrationsConfig:
    """Tests for the IntegrationsConfig."""

    def test_default_construction(self) -> None:
        config = IntegrationsConfig()
        assert config.enabled is True
        assert config.webhooks.rate_limit_rpm == 100
        assert config.webhooks.replay_window_seconds == 300
        assert config.health.check_interval_seconds == 300
        assert config.secret_backend.backend_type == "encrypted_sqlite"
        assert config.tunnel.auth_token_env == "NGROK_AUTHTOKEN"
        assert config.mcp_catalog.enabled is True

    def test_frozen(self) -> None:
        config = IntegrationsConfig()
        with pytest.raises(ValidationError):
            config.enabled = False  # type: ignore[misc]


@pytest.mark.unit
class TestOAuthStateConsumedPair:
    """The consumed_at + connection_name_returned pair invariant."""

    def _base_kwargs(self) -> dict[str, object]:
        now = datetime.now(UTC)
        return {
            "state_token": NotBlankStr("state-abc"),
            "connection_name": NotBlankStr("github"),
            "created_at": now - timedelta(minutes=1),
            "expires_at": now + timedelta(minutes=10),
        }

    def test_both_unset_is_valid(self) -> None:
        # Flow-start state: neither consumed_at nor
        # connection_name_returned is populated.
        state = OAuthState(**self._base_kwargs())  # type: ignore[arg-type]
        assert state.consumed_at is None
        assert state.connection_name_returned is None

    def test_both_set_is_valid(self) -> None:
        # Post-callback snapshot: mark_consumed stamps both
        # atomically; the pair must round-trip through validation.
        now = datetime.now(UTC)
        state = OAuthState(
            **self._base_kwargs(),  # type: ignore[arg-type]
            consumed_at=now,
            connection_name_returned=NotBlankStr("github"),
        )
        assert state.consumed_at == now
        assert state.connection_name_returned == "github"

    def test_only_consumed_at_set_raises(self) -> None:
        # Half-set state would let a redelivered callback see
        # consumed_at and route to the replay branch with no
        # connection name to return -- the validator must block this.
        with pytest.raises(
            ValidationError, match="consumed_at and connection_name_returned"
        ):
            OAuthState(
                **self._base_kwargs(),  # type: ignore[arg-type]
                consumed_at=datetime.now(UTC),
                connection_name_returned=None,
            )

    def test_only_connection_name_returned_set_raises(self) -> None:
        with pytest.raises(
            ValidationError, match="consumed_at and connection_name_returned"
        ):
            OAuthState(
                **self._base_kwargs(),  # type: ignore[arg-type]
                consumed_at=None,
                connection_name_returned=NotBlankStr("github"),
            )
