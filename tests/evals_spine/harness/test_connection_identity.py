# module-kind: tests
"""A connection is what it dispatches through, not the name naming it.

The journal header stamps a placeholder ``(provider, model_id)`` pair for
vendor-neutrality. Nothing else in the recorded artefact says whether the
system behind that name changed between two runs of the same matrix, so a
provider swap, an endpoint change or a re-pointed model passes ``--resume``
undetected. A credential rotation must NOT trip the same alarm: it is the one
change to a connection that is not a change to the system being measured.
"""

from datetime import UTC, datetime

import pytest

from evals.harness import connection_identity
from evals.harness.connection_identity import connection_sha256
from synthorg.config.provider_schema import ProviderConfig
from synthorg.providers.enums import AuthType

pytestmark = pytest.mark.unit


def _config(**overrides: object) -> ProviderConfig:
    """Build a minimal API-key connection, no catalog reference needed.

    Returns:
        The config.
    """
    base: dict[str, object] = {
        "auth_type": AuthType.API_KEY,
        "connection_name": "primary",
        "base_url": "https://example.test",
    }
    base.update(overrides)
    return ProviderConfig.model_validate(base)


class TestTheDigestNamesTheSystem:
    """What must change it, and what must not."""

    def test_the_same_connection_and_model_digest_identically(self) -> None:
        assert connection_sha256(_config(), model_id="m1") == connection_sha256(
            _config(), model_id="m1"
        )

    def test_a_different_model_on_the_same_connection_digests_differently(
        self,
    ) -> None:
        assert connection_sha256(_config(), model_id="m1") != connection_sha256(
            _config(), model_id="m2"
        )

    def test_a_different_endpoint_digests_differently(self) -> None:
        first = connection_sha256(_config(), model_id="m1")
        second = connection_sha256(
            _config(base_url="https://different.test"), model_id="m1"
        )

        assert first != second

    def test_a_credential_rotation_alone_does_not_change_the_digest(self) -> None:
        """The one change to a connection that is not a change of system."""
        accepted = datetime(2026, 1, 1, tzinfo=UTC)
        rotated = _config(
            auth_type=AuthType.SUBSCRIPTION,
            subscription_token="rotated-secret",
            tos_accepted_at=accepted,
            connection_name=None,
        )
        original = _config(
            auth_type=AuthType.SUBSCRIPTION,
            subscription_token="original-secret",
            tos_accepted_at=accepted,
            connection_name=None,
        )

        assert connection_sha256(original, model_id="m1") == connection_sha256(
            rotated, model_id="m1"
        )

    def test_an_oauth_secret_rotation_alone_does_not_change_the_digest(self) -> None:
        original = _config(
            auth_type=AuthType.OAUTH,
            connection_name=None,
            oauth_token_url="https://auth.example.test/token",
            oauth_client_id="client-1",
            oauth_client_secret="original-secret",
        )
        rotated = original.model_copy(update={"oauth_client_secret": "rotated-secret"})

        assert connection_sha256(original, model_id="m1") == connection_sha256(
            rotated, model_id="m1"
        )

    def test_a_custom_header_value_rotation_alone_does_not_change_the_digest(
        self,
    ) -> None:
        original = _config(
            auth_type=AuthType.CUSTOM_HEADER,
            connection_name=None,
            custom_header_name="X-Api-Key",
            custom_header_value="original-secret",
        )
        rotated = original.model_copy(update={"custom_header_value": "rotated-secret"})

        assert connection_sha256(original, model_id="m1") == connection_sha256(
            rotated, model_id="m1"
        )

    def test_the_catalog_reference_itself_is_not_excluded(self) -> None:
        """``connection_name`` is repr=False but names the system, not a secret."""
        first = connection_sha256(_config(connection_name="primary"), model_id="m1")
        second = connection_sha256(_config(connection_name="secondary"), model_id="m1")

        assert first != second


class TestTheExclusionGuard:
    """The module-level assertion that keeps the declared sets honest."""

    def test_every_repr_false_field_is_accounted_for(self) -> None:
        """Re-run of the guard the module already ran at import time.

        Not a regression test in the usual sense: if a new credential field
        is ever added to ``ProviderConfig`` as ``repr=False`` without being
        added to either declared set here, THIS is the assertion that must
        start failing, not a silent widening of the digest.
        """
        connection_identity._guard_declared_fields_are_real()
