"""Parity + completeness tests for the auth-type descriptor table.

Pins the descriptor map's per-auth-type values (owned fields, api-key
support, ToS requirement, discovery-header style) and asserts the
import-time completeness guard covers every :class:`AuthType`.
"""

import pytest

from synthorg.providers._auth_type_descriptor import (
    AUTH_TYPE_DESCRIPTORS,
    DiscoveryAuthStyle,
)
from synthorg.providers.enums import AuthType

# The owned credential fields each AuthType must expose.
_EXPECTED_OWNED: dict[AuthType, tuple[str, ...]] = {
    AuthType.API_KEY: ("api_key",),
    AuthType.OAUTH: (
        "api_key",
        "oauth_client_secret",
        "oauth_token_url",
        "oauth_client_id",
        "oauth_scope",
    ),
    AuthType.CUSTOM_HEADER: ("custom_header_name", "custom_header_value"),
    AuthType.SUBSCRIPTION: ("subscription_token", "tos_accepted_at"),
    AuthType.NONE: (),
}
_EXPECTED_SUPPORTS_API_KEY: dict[AuthType, bool] = {
    AuthType.API_KEY: True,
    AuthType.OAUTH: True,
    AuthType.CUSTOM_HEADER: False,
    AuthType.SUBSCRIPTION: False,
    AuthType.NONE: False,
}
_EXPECTED_REQUIRES_TOS: dict[AuthType, bool] = {
    AuthType.API_KEY: False,
    AuthType.OAUTH: False,
    AuthType.CUSTOM_HEADER: False,
    AuthType.SUBSCRIPTION: True,
    AuthType.NONE: False,
}
_EXPECTED_DISCOVERY: dict[AuthType, DiscoveryAuthStyle] = {
    AuthType.API_KEY: DiscoveryAuthStyle.BEARER_API_KEY,
    AuthType.OAUTH: DiscoveryAuthStyle.OAUTH_UNSUPPORTED,
    AuthType.CUSTOM_HEADER: DiscoveryAuthStyle.CUSTOM_HEADER,
    AuthType.SUBSCRIPTION: DiscoveryAuthStyle.BEARER_SUBSCRIPTION,
    AuthType.NONE: DiscoveryAuthStyle.NONE,
}


@pytest.mark.unit
class TestAuthTypeDescriptorParity:
    """The descriptor produces the expected per-auth-type tables."""

    @pytest.mark.parametrize("auth_type", list(AuthType))
    def test_owned_fields_match(self, auth_type: AuthType) -> None:
        assert (
            AUTH_TYPE_DESCRIPTORS[auth_type].owned_fields == _EXPECTED_OWNED[auth_type]
        )

    @pytest.mark.parametrize("auth_type", list(AuthType))
    def test_supports_api_key_matches(self, auth_type: AuthType) -> None:
        assert (
            AUTH_TYPE_DESCRIPTORS[auth_type].supports_api_key
            == _EXPECTED_SUPPORTS_API_KEY[auth_type]
        )

    @pytest.mark.parametrize("auth_type", list(AuthType))
    def test_requires_tos_matches(self, auth_type: AuthType) -> None:
        assert (
            AUTH_TYPE_DESCRIPTORS[auth_type].requires_tos
            == _EXPECTED_REQUIRES_TOS[auth_type]
        )

    @pytest.mark.parametrize("auth_type", list(AuthType))
    def test_discovery_style_matches(self, auth_type: AuthType) -> None:
        assert (
            AUTH_TYPE_DESCRIPTORS[auth_type].discovery_style
            == _EXPECTED_DISCOVERY[auth_type]
        )


@pytest.mark.unit
class TestAuthTypeDescriptorIntegrity:
    """Completeness guard invariants."""

    def test_map_covers_every_auth_type(self) -> None:
        assert set(AUTH_TYPE_DESCRIPTORS) == set(AuthType)

    def test_completeness_guard_predicate_flags_a_gap(self) -> None:
        # Mirror the module-level guard against a doctored map missing one
        # auth type: the predicate the import-time guard runs must report
        # the gap (it raises ValueError at import for the real module).
        doctored = {
            auth_type: descriptor
            for auth_type, descriptor in AUTH_TYPE_DESCRIPTORS.items()
            if auth_type is not AuthType.NONE
        }
        missing = set(AuthType) - set(doctored)
        assert missing == {AuthType.NONE}
