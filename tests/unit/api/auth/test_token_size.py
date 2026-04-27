"""Coverage for the security.auth_token_bytes resolution chain.

The auth-surface entropy budget resolves through
``ConfigResolver.get_int`` at process startup.  ``set_auth_token_bytes``
applies the resolved value to a process-wide cache; every subsequent
``secrets.token_urlsafe`` call across the auth surface (WS tickets,
CSRF, refresh tokens, OAuth state, API keys) reads from the cache.
The setter rejects values outside ``[16, 64]`` so a misconfigured
deployment cannot ship a 128-bit-or-weaker token format.
"""

from collections.abc import Iterator

import pytest

from synthorg.api.auth import token_size

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_token_size() -> Iterator[None]:
    # Module-global cache; restore the registered default after every
    # test so cross-test pollution cannot mask a regression.
    yield
    token_size.set_auth_token_bytes(token_size._DEFAULT_AUTH_TOKEN_BYTES)


def test_default_returns_registered_constant() -> None:
    assert token_size.get_auth_token_bytes() == token_size._DEFAULT_AUTH_TOKEN_BYTES


def test_setter_updates_module_cache() -> None:
    token_size.set_auth_token_bytes(48)
    assert token_size.get_auth_token_bytes() == 48


def test_setter_rejects_below_minimum() -> None:
    with pytest.raises(ValueError, match="out of range"):
        token_size.set_auth_token_bytes(15)


def test_setter_rejects_above_maximum() -> None:
    with pytest.raises(ValueError, match="out of range"):
        token_size.set_auth_token_bytes(65)


def test_setter_accepts_lower_bound() -> None:
    token_size.set_auth_token_bytes(token_size._MIN_AUTH_TOKEN_BYTES)
    assert token_size.get_auth_token_bytes() == token_size._MIN_AUTH_TOKEN_BYTES


def test_setter_accepts_upper_bound() -> None:
    token_size.set_auth_token_bytes(token_size._MAX_AUTH_TOKEN_BYTES)
    assert token_size.get_auth_token_bytes() == token_size._MAX_AUTH_TOKEN_BYTES
