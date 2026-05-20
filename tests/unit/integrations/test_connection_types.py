"""Unit tests for connection type authenticators."""

import pytest

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.connections.types import get_authenticator
from synthorg.integrations.errors import InvalidConnectionAuthError


@pytest.mark.unit
class TestGitHubAuthenticator:
    """Tests for GitHub connection validation."""

    def test_valid_credentials_accepted(self) -> None:
        auth = get_authenticator(ConnectionType.GITHUB)
        auth.validate_credentials({"token": "ghp_abc123"})

    def test_missing_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.GITHUB)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({})

    def test_empty_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.GITHUB)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({"token": "  "})

    def test_required_fields(self) -> None:
        auth = get_authenticator(ConnectionType.GITHUB)
        assert auth.required_fields() == ("token",)


@pytest.mark.unit
class TestGitLabAuthenticator:
    """Tests for GitLab connection validation."""

    def test_valid_credentials_accepted(self) -> None:
        auth = get_authenticator(ConnectionType.GITLAB)
        auth.validate_credentials({"token": "glpat-abc123"})

    def test_missing_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.GITLAB)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({})

    def test_empty_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.GITLAB)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({"token": "  "})

    def test_required_fields(self) -> None:
        auth = get_authenticator(ConnectionType.GITLAB)
        assert auth.required_fields() == ("token",)


@pytest.mark.unit
class TestGiteaForgejoAuthenticators:
    """Gitea/Forgejo share base code but report distinct identities."""

    @pytest.mark.parametrize(
        "ct",
        [ConnectionType.GITEA, ConnectionType.FORGEJO],
    )
    def test_valid_credentials_accepted(self, ct: ConnectionType) -> None:
        auth = get_authenticator(ct)
        auth.validate_credentials({"token": "tok-123"})

    @pytest.mark.parametrize(
        "ct",
        [ConnectionType.GITEA, ConnectionType.FORGEJO],
    )
    def test_missing_token_rejected(self, ct: ConnectionType) -> None:
        auth = get_authenticator(ct)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({})

    @pytest.mark.parametrize(
        ("ct", "token"),
        [
            (ConnectionType.GITEA, ""),
            (ConnectionType.GITEA, "   "),
            (ConnectionType.FORGEJO, ""),
            (ConnectionType.FORGEJO, "   "),
        ],
    )
    def test_empty_or_whitespace_token_rejected(
        self, ct: ConnectionType, token: str
    ) -> None:
        """Empty / whitespace-only tokens must reject just like missing ones.

        Keeps token-validation parity with the GitHub and GitLab
        authenticators, where the shared validator already rejects
        blank strings; the Gitea/Forgejo subclasses share the same
        base but the test suite covered only the missing-key case.
        """
        auth = get_authenticator(ct)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({"token": token})

    def test_distinct_connection_type_identity(self) -> None:
        gitea = get_authenticator(ConnectionType.GITEA)
        forgejo = get_authenticator(ConnectionType.FORGEJO)
        assert gitea.connection_type is ConnectionType.GITEA
        assert forgejo.connection_type is ConnectionType.FORGEJO
        assert type(gitea).__mro__[1] is type(forgejo).__mro__[1]


@pytest.mark.unit
class TestSlackAuthenticator:
    """Tests for Slack connection validation."""

    def test_valid_credentials_accepted(self) -> None:
        auth = get_authenticator(ConnectionType.SLACK)
        auth.validate_credentials({"token": "xoxb-test"})

    def test_missing_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.SLACK)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({})


@pytest.mark.unit
class TestSmtpAuthenticator:
    """Tests for SMTP connection validation."""

    def test_valid_credentials_with_auth(self) -> None:
        auth = get_authenticator(ConnectionType.SMTP)
        auth.validate_credentials(
            {
                "host": "smtp.example.com",
                "username": "user",
                "password": "pass",
            }
        )

    def test_valid_credentials_without_auth(self) -> None:
        auth = get_authenticator(ConnectionType.SMTP)
        auth.validate_credentials({"host": "smtp.example.com"})

    def test_missing_host_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.SMTP)
        with pytest.raises(InvalidConnectionAuthError, match="host"):
            auth.validate_credentials({})

    def test_partial_credentials_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.SMTP)
        with pytest.raises(InvalidConnectionAuthError, match="both"):
            auth.validate_credentials(
                {
                    "host": "smtp.example.com",
                    "username": "user",
                }
            )


@pytest.mark.unit
class TestDatabaseAuthenticator:
    """Tests for database connection validation."""

    def test_valid_postgres(self) -> None:
        auth = get_authenticator(ConnectionType.DATABASE)
        auth.validate_credentials(
            {
                "dialect": "postgres",
                "host": "localhost",
                "database": "mydb",
            }
        )

    def test_valid_sqlite(self) -> None:
        auth = get_authenticator(ConnectionType.DATABASE)
        auth.validate_credentials(
            {
                "dialect": "sqlite",
                "database": "/data/test.db",
            }
        )

    def test_missing_dialect_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.DATABASE)
        with pytest.raises(InvalidConnectionAuthError, match="dialect"):
            auth.validate_credentials({"database": "mydb"})

    def test_unknown_dialect_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.DATABASE)
        with pytest.raises(InvalidConnectionAuthError, match="Unknown"):
            auth.validate_credentials(
                {
                    "dialect": "oracle",
                    "database": "mydb",
                }
            )

    def test_postgres_without_host_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.DATABASE)
        with pytest.raises(InvalidConnectionAuthError, match="host"):
            auth.validate_credentials(
                {
                    "dialect": "postgres",
                    "database": "mydb",
                }
            )


@pytest.mark.unit
class TestGenericHttpAuthenticator:
    """Tests for generic HTTP connection validation."""

    def test_valid_credentials(self) -> None:
        auth = get_authenticator(ConnectionType.GENERIC_HTTP)
        auth.validate_credentials(
            {
                "base_url": "https://api.example.com",
            }
        )

    def test_missing_base_url_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.GENERIC_HTTP)
        with pytest.raises(InvalidConnectionAuthError, match="base_url"):
            auth.validate_credentials({})


@pytest.mark.unit
class TestOAuthAppAuthenticator:
    """Tests for OAuth app connection validation."""

    def test_valid_credentials(self) -> None:
        auth = get_authenticator(ConnectionType.OAUTH_APP)
        auth.validate_credentials(
            {
                "client_id": "cid",
                "client_secret": "csec",
                "auth_url": "https://provider.com/auth",
                "token_url": "https://provider.com/token",
            }
        )

    @pytest.mark.parametrize(
        "missing_field",
        ["client_id", "client_secret", "auth_url", "token_url"],
    )
    def test_missing_required_field_rejected(
        self,
        missing_field: str,
    ) -> None:
        auth = get_authenticator(ConnectionType.OAUTH_APP)
        creds = {
            "client_id": "cid",
            "client_secret": "csec",
            "auth_url": "https://provider.com/auth",
            "token_url": "https://provider.com/token",
        }
        del creds[missing_field]
        with pytest.raises(
            InvalidConnectionAuthError,
            match=missing_field,
        ):
            auth.validate_credentials(creds)


@pytest.mark.unit
class TestConnectionTypeRegistry:
    """Tests for the connection type registry."""

    def test_all_types_registered(self) -> None:
        for ct in ConnectionType:
            auth = get_authenticator(ct)
            assert auth.connection_type == ct

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(KeyError):
            get_authenticator("nonexistent")  # type: ignore[arg-type]
