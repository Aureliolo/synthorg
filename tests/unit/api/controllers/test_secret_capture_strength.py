"""Strength floor on a captured webhook signing secret.

The value is compared against a header on an endpoint reachable without
credentials, and GitLab's webhook scheme is a bare equality check binding neither
the body nor a timestamp, so a short secret is guessable inside the per-IP rate
limit. A hit there lets an attacker submit authored payloads as verified, not
merely replay a captured one.

The floor is scoped to this one field: a database password is checked by the
database, behind no unauthenticated endpoint, and rejecting a short one would
break a legitimate setup.
"""

import pytest
from pydantic import SecretStr, ValidationError

from synthorg.api.controllers.connections_models import SecretCaptureRequest
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.field_metadata import (
    WEBHOOK_SIGNING_SECRET_FIELD,
)

pytestmark = pytest.mark.unit

_LONG_ENOUGH = "a-secret-of-ample-length"


def _capture(
    value: str,
    kind: str = WEBHOOK_SIGNING_SECRET_FIELD,
) -> SecretCaptureRequest:
    """Build a capture request for *value* under *kind*.

    Returns:
        The constructed request.
    """
    return SecretCaptureRequest(value=SecretStr(value), secret_kind=NotBlankStr(kind))


class TestSigningSecretFloor:
    """Only the signing secret is held to a minimum length."""

    def test_a_long_signing_secret_is_accepted(self) -> None:
        assert _capture(_LONG_ENOUGH).value.get_secret_value() == _LONG_ENOUGH

    @pytest.mark.parametrize("value", ["x", "short", "0123456789012345"[:15]])
    def test_a_short_signing_secret_is_refused(self, value: str) -> None:
        with pytest.raises(ValidationError):
            _capture(value)

    def test_exactly_the_floor_is_accepted(self) -> None:
        assert _capture("0123456789abcdef").value.get_secret_value() != ""

    @pytest.mark.parametrize("value", ["   ", "\t\n ", " " * 40])
    def test_a_whitespace_only_signing_secret_is_refused(self, value: str) -> None:
        """Blank is not a secret, however long the string is."""
        with pytest.raises(ValidationError):
            _capture(value)

    def test_padding_does_not_count_towards_the_floor(self) -> None:
        with pytest.raises(ValidationError):
            _capture(f"  short  {' ' * 40}")

    def test_the_error_never_carries_the_value(self) -> None:
        """A rejection must not echo plaintext into a log or a response."""
        secret = "hunter2"
        with pytest.raises(ValidationError) as excinfo:
            _capture(secret)
        assert secret not in str(excinfo.value)


class TestOtherCredentialsAreUnaffected:
    """A short password stays legal; the floor is not a global rule."""

    @pytest.mark.parametrize("kind", ["token", "password", "api_key", "client_secret"])
    def test_a_short_value_of_another_kind_is_accepted(self, kind: str) -> None:
        assert _capture("s3cr3t", kind=kind).secret_kind == kind
