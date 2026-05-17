"""``/auth/refresh`` must bypass the auth middleware.

Refresh-token rotation runs exactly when the access token is expired,
so the route cannot itself require a valid access token. It must be in
the derived exclude set both by default AND when an operator supplies
a custom ``auth.exclude_paths`` (fail-safe fold), mirroring
login/logout.
"""

import pytest

from synthorg.api.middleware_factory import _build_auth_exclude_paths
from synthorg.core.auth.config import AuthConfig

pytestmark = pytest.mark.unit

_PREFIX = "/api/v1"
_WS = f"^{_PREFIX}/ws$"
_REFRESH = f"^{_PREFIX}/auth/refresh$"


def test_refresh_excluded_by_default() -> None:
    paths = _build_auth_exclude_paths(
        AuthConfig(jwt_secret="x" * 32),
        _PREFIX,
        _WS,
    )
    assert _REFRESH in paths


def test_refresh_folded_into_custom_override() -> None:
    # Operator narrows exclude_paths and omits refresh; the mandatory
    # fail-safe fold must still add it back so rotation never locks out.
    custom = AuthConfig(
        jwt_secret="x" * 32,
        exclude_paths=(f"^{_PREFIX}/auth/login$",),
    )
    paths = _build_auth_exclude_paths(custom, _PREFIX, _WS)
    assert _REFRESH in paths
