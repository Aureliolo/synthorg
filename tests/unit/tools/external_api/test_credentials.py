"""Tests for credential -> auth-header mapping."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.http_vendor import (
    HTTP_VENDOR_PRESETS,
    METADATA_KEY_VENDOR,
    HttpVendor,
)
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionHealth,
    ConnectionStatus,
    ConnectionType,
)
from synthorg.tools.external_api._credentials import (
    build_auth_headers,
    build_connection_auth_headers,
)
from synthorg.tools.external_api.errors import ExternalApiCredentialError


def _connection(
    vendor: str | None,
    *,
    auth_method: AuthMethod = AuthMethod.API_KEY,
) -> Connection:
    """Build a generic-HTTP connection, optionally bound to *vendor*.

    Returns:
        The connection.
    """
    return Connection(
        name=NotBlankStr("search"),
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=auth_method,
        base_url=NotBlankStr("https://api.example.test"),
        secret_refs=(),
        metadata={METADATA_KEY_VENDOR: vendor} if vendor else {},
        health=ConnectionHealth(status=ConnectionStatus.UNKNOWN),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.unit
class TestBuildAuthHeaders:
    def test_bearer_token(self) -> None:
        headers = build_auth_headers(AuthMethod.BEARER_TOKEN, {"token": "t1"})
        assert headers == {"Authorization": "Bearer t1"}

    def test_bearer_token_missing_raises(self) -> None:
        with pytest.raises(ExternalApiCredentialError):
            build_auth_headers(AuthMethod.BEARER_TOKEN, {})

    def test_oauth2_uses_token(self) -> None:
        headers = build_auth_headers(AuthMethod.OAUTH2, {"token": "abc"})
        assert headers == {"Authorization": "Bearer abc"}

    def test_oauth2_falls_back_to_access_token(self) -> None:
        headers = build_auth_headers(AuthMethod.OAUTH2, {"access_token": "xyz"})
        assert headers == {"Authorization": "Bearer xyz"}

    def test_oauth2_missing_raises(self) -> None:
        with pytest.raises(ExternalApiCredentialError):
            build_auth_headers(AuthMethod.OAUTH2, {})

    def test_api_key_default_header(self) -> None:
        headers = build_auth_headers(AuthMethod.API_KEY, {"api_key": "k1"})
        assert headers == {"X-API-Key": "k1"}

    @pytest.mark.parametrize("field", ["api_key", "token", "access_token"])
    def test_api_key_accepts_every_key_field_name(self, field: str) -> None:
        # The generic-HTTP form stores the secret as ``token``; accepting only
        # ``api_key`` would reject every connection the dashboard produces
        # while its health probe, which accepts all three, reported healthy.
        assert build_auth_headers(AuthMethod.API_KEY, {field: "k1"}) == {
            "X-API-Key": "k1"
        }

    def test_api_key_custom_header_pair(self) -> None:
        headers = build_auth_headers(
            AuthMethod.API_KEY,
            {"header_name": "X-Custom-Key", "header_value": "v1"},
        )
        assert headers == {"X-Custom-Key": "v1"}

    def test_api_key_missing_raises(self) -> None:
        with pytest.raises(ExternalApiCredentialError):
            build_auth_headers(AuthMethod.API_KEY, {})

    def test_basic_auth(self) -> None:
        headers = build_auth_headers(
            AuthMethod.BASIC_AUTH,
            {"username": "u", "password": "p"},
        )
        # base64("u:p") == "dTpw"
        assert headers == {"Authorization": "Basic dTpw"}

    def test_basic_auth_missing_raises(self) -> None:
        with pytest.raises(ExternalApiCredentialError):
            build_auth_headers(AuthMethod.BASIC_AUTH, {"username": "u"})

    def test_custom_with_header_pair(self) -> None:
        headers = build_auth_headers(
            AuthMethod.CUSTOM,
            {"header_name": "X-Sig", "header_value": "s1"},
        )
        assert headers == {"X-Sig": "s1"}

    def test_custom_empty_returns_no_headers(self) -> None:
        # CUSTOM permits no-auth (e.g. a public endpoint); empty is not an error.
        assert build_auth_headers(AuthMethod.CUSTOM, {}) == {}


@pytest.mark.unit
class TestBuildConnectionAuthHeaders:
    """A vendor names the header its API accepts; the generic guess is wrong."""

    def test_vendor_preset_renders_its_own_header(self) -> None:
        headers = build_connection_auth_headers(
            _connection(HttpVendor.BRAVE.value), {"token": "k"}
        )

        assert headers == {"X-Subscription-Token": "k"}
        assert "X-API-Key" not in headers

    @pytest.mark.parametrize("field", ["api_key", "token", "access_token"])
    def test_any_key_field_satisfies_a_preset(self, field: str) -> None:
        headers = build_connection_auth_headers(
            _connection(HttpVendor.TAVILY.value), {field: "k"}
        )

        assert headers == {"Authorization": "Bearer k"}

    def test_explicit_header_pair_wins_over_the_preset(self) -> None:
        # An operator who spelled the header out means it.
        headers = build_connection_auth_headers(
            _connection(HttpVendor.BRAVE.value),
            {"header_name": "X-Custom", "header_value": "v", "token": "k"},
        )

        assert headers == {"X-Custom": "v"}

    def test_a_preset_without_a_key_raises(self) -> None:
        with pytest.raises(ExternalApiCredentialError, match="Brave"):
            build_connection_auth_headers(_connection(HttpVendor.BRAVE.value), {})

    def test_no_vendor_falls_back_to_the_generic_mapping(self) -> None:
        headers = build_connection_auth_headers(_connection(None), {"token": "k"})

        assert headers == {"X-API-Key": "k"}

    def test_an_unrecognised_vendor_does_not_guess(self) -> None:
        # Resolution fails safe to "no preset", which is the generic mapping,
        # never a vendor picked by resemblance.
        headers = build_connection_auth_headers(_connection("nope"), {"token": "k"})

        assert headers == {"X-API-Key": "k"}

    def test_every_preset_sends_the_key_it_was_given(self) -> None:
        # A template that never names {key} renders a constant header and
        # drops the secret; the model rejects one, and this pins the effect.
        for preset in HTTP_VENDOR_PRESETS.values():
            assert "sentinel" in "".join(preset.auth_headers("sentinel").values())
