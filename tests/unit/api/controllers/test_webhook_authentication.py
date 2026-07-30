"""Ingest refuses a delivery to a connection the signing secret no longer covers.

``webhook_ingest_is_reachable`` is unit-tested against the registry in
``tests/unit/integrations/test_field_metadata.py``; what is tested here is that
the ingest path actually consults it, so a stored secret stops authenticating
the moment the operator repoints the connection at an outbound vendor preset.
"""

import hashlib
import hmac
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers.webhooks._authentication import verify_signature
from synthorg.core.domain_errors import UnauthorizedError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
from tests._shared import mock_of

_SECRET = "supersecret"
_BODY = b'{"hello":1}'


def _generic_http(vendor: str) -> Connection:
    return Connection(
        name=NotBlankStr("c1"),
        connection_type=ConnectionType.GENERIC_HTTP,
        auth_method=AuthMethod.API_KEY,
        base_url=NotBlankStr("https://example.com"),
        metadata={"vendor": vendor},
    )


def _signed_headers() -> dict[str, str]:
    digest = hmac.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    return {"X-Signature": digest}


def _catalog() -> ConnectionCatalog:
    catalog: ConnectionCatalog = mock_of[ConnectionCatalog](
        get_credentials=AsyncMock(return_value={"signing_secret": _SECRET}),
    )
    return catalog


@pytest.mark.unit
class TestSigningSecretAppliesToTheConnection:
    async def test_a_custom_vendor_delivery_verifies(self) -> None:
        await verify_signature(
            catalog=_catalog(),
            connection=_generic_http("custom"),
            body=_BODY,
            headers=_signed_headers(),
        )

    async def test_a_preset_vendor_delivery_is_refused_despite_a_valid_signature(
        self,
    ) -> None:
        """The signature matches and the secret is stored; only the condition fails.

        Credentials cannot be cleared through an update and the dashboard has
        stopped showing the field, so without this check the retired inbound path
        would keep publishing verified events with nothing on screen to say so.
        """
        with pytest.raises(UnauthorizedError):
            await verify_signature(
                catalog=_catalog(),
                connection=_generic_http("some-preset"),
                body=_BODY,
                headers=_signed_headers(),
            )
