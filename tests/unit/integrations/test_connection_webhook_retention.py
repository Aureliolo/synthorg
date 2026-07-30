"""Validation guards for ``Connection.webhook_receipt_retention_days``.

The field carries a tri-state contract on the wire:

* ``None`` (default) -- fall back to the global
  ``integrations.webhook_receipt_retention_days`` setting.
* ``0`` -- opt this connection out of webhook-receipt sweeping.
* positive integer -- connection-specific retention window in days.

Negative values must raise so an operator typo cannot silently truncate
the receipt log via the cleanup loop.
"""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.integrations.config import WebhooksConfig
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit


def _connection(**overrides: object) -> Connection:
    """Build a minimal :class:`Connection` with ``overrides`` applied."""
    fields: dict[str, object] = {
        "name": NotBlankStr("c1"),
        "connection_type": ConnectionType.GENERIC_HTTP,
        "auth_method": AuthMethod.API_KEY,
    }
    fields.update(overrides)
    return Connection(**fields)  # type: ignore[arg-type]


class TestWebhookReceiptRetentionDaysField:
    def test_default_is_none(self) -> None:
        assert _connection().webhook_receipt_retention_days is None

    def test_zero_accepted(self) -> None:
        assert (
            _connection(webhook_receipt_retention_days=0).webhook_receipt_retention_days
            == 0
        )

    def test_positive_accepted(self) -> None:
        assert (
            _connection(
                webhook_receipt_retention_days=90
            ).webhook_receipt_retention_days
            == 90
        )

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _connection(webhook_receipt_retention_days=-1)


class TestWebhooksConfigRetentionBound:
    """The static mirror must admit every value the setting does.

    The mirror is unconditional, so the registry default lands on every
    ``WebhooksConfig`` construction. A lower bound above ``0`` would therefore
    reject the documented opt-out and fail config construction at boot for every
    deployment, not just one that opted out.
    """

    def test_zero_accepted(self) -> None:
        assert WebhooksConfig(receipt_retention_days=0).receipt_retention_days == 0

    def test_the_default_is_never_sweep(self) -> None:
        assert WebhooksConfig().receipt_retention_days == 0

    def test_positive_accepted(self) -> None:
        assert WebhooksConfig(receipt_retention_days=30).receipt_retention_days == 30

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WebhooksConfig(receipt_retention_days=-1)

    def test_the_registry_default_matches_the_mirror_default(self) -> None:
        """Two sources for one value, so they are asserted equal by name."""
        registered = get_registry().get(
            SettingNamespace.INTEGRATIONS.value,
            "webhook_receipt_retention_days",
        )
        assert registered is not None
        assert registered.default is not None
        assert int(registered.default) == WebhooksConfig().receipt_retention_days
