"""Unit tests for connection type authenticators."""

import pytest
from typeguard import suppress_type_checks

from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.connections.protocol import AUTH_METHOD_VIEW_KEY
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
class TestTunnelAuthenticator:
    """Tunnel connections back a token OR a no-secret device login."""

    def test_token_credentials_accepted(self) -> None:
        auth = get_authenticator(ConnectionType.TUNNEL)
        auth.validate_credentials({"auth_token": "tok-123"})

    def test_empty_credentials_accepted_for_device_login(self) -> None:
        auth = get_authenticator(ConnectionType.TUNNEL)
        auth.validate_credentials({})

    def test_blank_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.TUNNEL)
        with pytest.raises(InvalidConnectionAuthError, match="auth_token"):
            auth.validate_credentials({"auth_token": "  "})

    def test_no_field_universally_required(self) -> None:
        auth = get_authenticator(ConnectionType.TUNNEL)
        assert auth.required_fields() == ()


@pytest.mark.unit
class TestLLMProviderAuthenticator:
    """Tests for LLM-provider credential validation."""

    def test_valid_credentials_accepted(self) -> None:
        auth = get_authenticator(ConnectionType.LLM_PROVIDER)
        auth.validate_credentials({"api_key": "sk-test-123"})

    def test_missing_api_key_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.LLM_PROVIDER)
        with pytest.raises(InvalidConnectionAuthError, match="api_key"):
            auth.validate_credentials({})

    def test_blank_api_key_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.LLM_PROVIDER)
        with pytest.raises(InvalidConnectionAuthError, match="api_key"):
            auth.validate_credentials({"api_key": "   "})

    def test_non_string_api_key_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.LLM_PROVIDER)
        # A non-string api_key (e.g. a JSON number) must be rejected, not
        # coerced; suppress_type_checks lets the int reach the validator.
        with (
            pytest.raises(InvalidConnectionAuthError, match="api_key"),
            suppress_type_checks(),
        ):
            auth.validate_credentials({"api_key": 123})  # type: ignore[dict-item]

    def test_base_url_not_required(self) -> None:
        # Unlike GENERIC_HTTP, no base_url is needed: providers routing
        # through litellm's default endpoints have none of their own.
        auth = get_authenticator(ConnectionType.LLM_PROVIDER)
        assert auth.required_fields() == ("api_key",)


@pytest.mark.unit
class TestDeployAuthenticator:
    """Tests for deploy-target credential validation."""

    def test_valid_credentials_accepted(self) -> None:
        auth = get_authenticator(ConnectionType.DEPLOY)
        auth.validate_credentials({"token": "platform-token"})

    def test_missing_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.DEPLOY)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({})

    def test_blank_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.DEPLOY)
        with pytest.raises(InvalidConnectionAuthError, match="token"):
            auth.validate_credentials({"token": "   "})

    def test_non_string_token_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.DEPLOY)
        with (
            pytest.raises(InvalidConnectionAuthError, match="token"),
            suppress_type_checks(),
        ):
            auth.validate_credentials({"token": 123})  # type: ignore[dict-item]

    def test_only_the_token_is_required(self) -> None:
        # Platform / environment / project ride in connection metadata, not
        # credentials, so the token is the sole required credential field.
        auth = get_authenticator(ConnectionType.DEPLOY)
        assert auth.required_fields() == ("token",)

    def test_connection_type_identity(self) -> None:
        auth = get_authenticator(ConnectionType.DEPLOY)
        assert auth.connection_type is ConnectionType.DEPLOY


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

    @pytest.mark.parametrize(
        "credentials",
        [
            {"token": "t"},
            {"api_key": "k"},
            {"access_token": "a"},
            {"header_name": "X-Key", "header_value": "v"},
            {"username": "u", "password": "p"},
        ],
        ids=["token", "api_key", "access_token", "header-pair", "basic"],
    )
    def test_valid_credentials(self, credentials: dict[str, str]) -> None:
        auth = get_authenticator(ConnectionType.GENERIC_HTTP)
        auth.validate_credentials({"base_url": "https://api.example.com"} | credentials)

    def test_missing_base_url_rejected(self) -> None:
        auth = get_authenticator(ConnectionType.GENERIC_HTTP)
        with pytest.raises(InvalidConnectionAuthError, match="base_url"):
            auth.validate_credentials({})

    def test_missing_credential_material_rejected(self) -> None:
        # A vendor preset supplies the base URL, so without this the only
        # field this type enforced became optional and a connection with no
        # way to authenticate would be created with no friction at all.
        auth = get_authenticator(ConnectionType.GENERIC_HTTP)
        with pytest.raises(InvalidConnectionAuthError, match="credential material"):
            auth.validate_credentials({"base_url": "https://api.example.com"})

    @pytest.mark.parametrize(
        "credentials",
        [
            {"header_name": "X-Key"},
            {"header_value": "v"},
            {"username": "u"},
            {"password": "p"},
            {"token": "   "},
        ],
        ids=[
            "half-header",
            "half-header-value",
            "half-basic",
            "half-basic-password",
            "blank-token",
        ],
    )
    def test_partial_credential_material_rejected(
        self, credentials: dict[str, str]
    ) -> None:
        auth = get_authenticator(ConnectionType.GENERIC_HTTP)
        with pytest.raises(InvalidConnectionAuthError, match="credential material"):
            auth.validate_credentials(
                {"base_url": "https://api.example.com"} | credentials
            )

    @pytest.mark.parametrize(
        ("auth_method", "credentials"),
        [
            (AuthMethod.BASIC_AUTH, {"token": "t"}),
            (AuthMethod.BASIC_AUTH, {"api_key": "k"}),
            (AuthMethod.API_KEY, {"username": "u", "password": "p"}),
            (AuthMethod.BEARER_TOKEN, {"username": "u", "password": "p"}),
            (AuthMethod.OAUTH2, {"username": "u", "password": "p"}),
        ],
        ids=[
            "basic-with-token",
            "basic-with-api-key",
            "api-key-with-basic",
            "bearer-with-basic",
            "oauth2-with-basic",
        ],
    )
    def test_material_for_another_method_rejected(
        self, auth_method: AuthMethod, credentials: dict[str, str]
    ) -> None:
        # Material the declared method never reads fails every call the
        # connection makes, so accepting it only moves the discovery from
        # create-time to first use, where it reads as an upstream fault.
        auth = get_authenticator(ConnectionType.GENERIC_HTTP)
        with pytest.raises(InvalidConnectionAuthError, match="declared auth method"):
            auth.validate_credentials(
                {
                    "base_url": "https://api.example.com",
                    AUTH_METHOD_VIEW_KEY: auth_method.value,
                }
                | credentials
            )

    @pytest.mark.parametrize(
        ("auth_method", "credentials"),
        [
            (AuthMethod.BASIC_AUTH, {"username": "u", "password": "p"}),
            (AuthMethod.API_KEY, {"api_key": "k"}),
            (AuthMethod.BEARER_TOKEN, {"token": "t"}),
            (AuthMethod.OAUTH2, {"access_token": "a"}),
            # A custom header is the escape hatch for a service none of the
            # standard methods describes, so it satisfies any of them.
            (AuthMethod.BASIC_AUTH, {"header_name": "X-Key", "header_value": "v"}),
            # CUSTOM names no shape, so anything usable counts.
            (AuthMethod.CUSTOM, {"token": "t"}),
        ],
        ids=[
            "basic",
            "api-key",
            "bearer",
            "oauth2",
            "header-escape-hatch",
            "custom",
        ],
    )
    def test_material_matching_the_method_accepted(
        self, auth_method: AuthMethod, credentials: dict[str, str]
    ) -> None:
        auth = get_authenticator(ConnectionType.GENERIC_HTTP)
        auth.validate_credentials(
            {
                "base_url": "https://api.example.com",
                AUTH_METHOD_VIEW_KEY: auth_method.value,
            }
            | credentials
        )


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
        with suppress_type_checks(), pytest.raises(KeyError):
            get_authenticator("nonexistent")  # type: ignore[arg-type]
