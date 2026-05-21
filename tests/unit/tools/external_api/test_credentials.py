"""Tests for credential -> auth-header mapping (build_auth_headers)."""

import pytest

from synthorg.integrations.connections.models import AuthMethod
from synthorg.tools.external_api._credentials import build_auth_headers
from synthorg.tools.external_api.errors import ExternalApiCredentialError


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
