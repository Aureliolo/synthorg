"""OIDC nonce generation + id_token surfacing in the auth-code flow.

``start_flow`` must mint an OIDC nonce, bind it to the persisted
``OAuthState``, and include it in the authorization request so the
provider echoes it into the issued ``id_token``. ``_parse_token_response``
must surface ``id_token`` (fail-fast on a non-string, like the other
optional fields) so the callback can verify the nonce claim.
"""

from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest

from synthorg.integrations.errors import TokenExchangeFailedError
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)

pytestmark = pytest.mark.unit

# Fixed valid Fernet key so the PKCE verifier cipher initialises in
# ``start_flow`` (same key the integration-flow conftest uses).
_TEST_MASTER_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.fixture(autouse=True)
def _set_master_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide ``SYNTHORG_MASTER_KEY`` + reset the cached cipher."""
    from synthorg.integrations.oauth.pkce import _reset_cipher_for_tests

    monkeypatch.setenv("SYNTHORG_MASTER_KEY", _TEST_MASTER_KEY)
    _reset_cipher_for_tests()
    yield
    _reset_cipher_for_tests()


async def test_start_flow_generates_nonce_in_url_and_state() -> None:
    flow = AuthorizationCodeFlow()

    auth_url, state = await flow.start_flow(
        auth_url="https://idp.example.com/authorize",
        token_url="https://idp.example.com/token",
        client_id="cid",
        client_secret="csec",
        scopes=("openid", "email"),
        redirect_uri="https://app.example.com/cb",
    )

    assert state.nonce is not None
    assert len(state.nonce) >= 16
    query = parse_qs(urlparse(auth_url).query)
    assert query["nonce"] == [str(state.nonce)]
    # Nonce and state token are independent secrets.
    assert state.nonce != state.state_token


def test_parse_token_response_surfaces_id_token() -> None:
    flow = AuthorizationCodeFlow()

    token = flow._parse_token_response(
        {"access_token": "atk", "id_token": "h.p.s"},
        "exchange",
    )

    assert token.id_token == "h.p.s"


def test_parse_token_response_id_token_absent_is_none() -> None:
    flow = AuthorizationCodeFlow()

    token = flow._parse_token_response({"access_token": "atk"}, "exchange")

    assert token.id_token is None


def test_parse_token_response_rejects_non_string_id_token() -> None:
    flow = AuthorizationCodeFlow()

    with pytest.raises(TokenExchangeFailedError, match="id_token"):
        flow._parse_token_response(
            {"access_token": "atk", "id_token": 123},
            "exchange",
        )
