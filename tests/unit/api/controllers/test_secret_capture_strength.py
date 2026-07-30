"""Strength floor on a captured webhook signing secret.

The value is compared against a header on an endpoint reachable without
credentials, and GitLab's webhook scheme is a bare equality check binding neither
the body nor a timestamp, so a short secret is guessable inside the per-IP rate
limit. A hit there lets an attacker submit authored payloads as verified, not
merely replay a captured one.

The floor is scoped to this one field: a database password is checked by the
database, behind no unauthenticated endpoint, and rejecting a short one would
break a legitimate setup. It keys on the field the handle binds to, which is
what decides where the value lands, rather than on the caller's own
``secret_kind`` label, which is free text.
"""

import pytest
from pydantic import SecretStr

from synthorg.api.controllers._connection_secrets import capture_secret_value
from synthorg.api.controllers.connections_models import (
    SecretCaptureRequest,
    signing_secret_floor_error,
)
from synthorg.core.domain_errors import ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.field_metadata import (
    WEBHOOK_SIGNING_SECRET_FIELD,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_LONG_ENOUGH = "a-secret-of-ample-length"


def _refusal(value: str, field: str = WEBHOOK_SIGNING_SECRET_FIELD) -> str | None:
    """Evaluate the floor for *value* bound to *field*.

    Returns:
        The refusal message, or ``None`` when the value is acceptable.
    """
    return signing_secret_floor_error(field_name=field, value=SecretStr(value))


class TestSigningSecretFloor:
    """Only the signing-secret field is held to a minimum length."""

    def test_a_long_signing_secret_is_accepted(self) -> None:
        assert _refusal(_LONG_ENOUGH) is None

    @pytest.mark.parametrize("value", ["x", "short", "0123456789012345"[:15]])
    def test_a_short_signing_secret_is_refused(self, value: str) -> None:
        assert _refusal(value) is not None

    def test_exactly_the_floor_is_accepted(self) -> None:
        assert _refusal("0123456789abcdef") is None

    @pytest.mark.parametrize("value", ["   ", "\t\n ", " " * 40])
    def test_a_whitespace_only_signing_secret_is_refused(self, value: str) -> None:
        """Blank is not a secret, however long the string is."""
        assert _refusal(value) is not None

    def test_padding_does_not_count_towards_the_floor(self) -> None:
        assert _refusal(f"  short  {' ' * 40}") is not None

    def test_the_error_never_carries_the_value(self) -> None:
        """A rejection must not echo plaintext into a log or a response."""
        secret = "hunter2"
        message = _refusal(secret)
        assert message is not None
        assert secret not in message


class TestOtherFieldsAreUnaffected:
    """A short password stays legal; the floor is not a global rule."""

    @pytest.mark.parametrize("field", ["token", "password", "api_key", "client_secret"])
    def test_a_short_value_bound_to_another_field_is_accepted(self, field: str) -> None:
        assert _refusal("s3cr3t", field=field) is None


class TestTheLabelCannotBuyAnExemption:
    """The capture endpoint keys on its own path, not on the caller's label."""

    async def test_a_mislabelled_short_signing_secret_is_still_refused(self) -> None:
        """The bypass this floor exists to close.

        ``secret_kind`` is free text the caller writes; the URL's field is what
        the handle binds to and therefore what the value ends up authenticating.
        Keyed on the label, four characters captured as some other kind would
        bind to the signing-secret field having met no floor at all.
        """
        with pytest.raises(ValidationError):
            await capture_secret_value(
                make_app_state(),
                "draft-1",
                WEBHOOK_SIGNING_SECRET_FIELD,
                SecretCaptureRequest(
                    value=SecretStr("abcd"),
                    secret_kind=NotBlankStr("token"),
                ),
            )
