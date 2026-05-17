"""Probe-miss logging must not attach tracebacks on the network path.

``_probe_and_fetch`` runs HTTP requests whose frame locals can hold
OAuth tokens. The unexpected-error branch must log a scrubbed
description (``error_type`` + ``error``) at WARNING with NO ``exc_info``
(a traceback serialises frame locals), and the probed URL must stay
redacted.
"""

from unittest.mock import AsyncMock, patch

import pytest
import structlog

from synthorg.observability.events.provider import PROVIDER_PROBE_MISS
from synthorg.providers.probing import _probe_and_fetch
from synthorg.providers.url_utils import redact_url

pytestmark = pytest.mark.unit

_CRED_URL = "http://svc:p@ss@models.example.com/models?api_key=topsecret999"


def _mock_client(*, side_effect: Exception) -> AsyncMock:
    """httpx.AsyncClient mock whose ``get`` raises *side_effect*."""
    client = AsyncMock()
    client.get.side_effect = side_effect
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def test_unexpected_error_logs_scrubbed_without_exc_info() -> None:
    boom = RuntimeError("upstream blew up; bearer topsecret999")
    with (
        patch("synthorg.providers.probing.httpx.AsyncClient") as mock_cls,
        structlog.testing.capture_logs() as logs,
    ):
        mock_cls.return_value = _mock_client(side_effect=boom)
        result = await _probe_and_fetch(_CRED_URL, "example-preset")

    assert result is None
    misses = [
        log
        for log in logs
        if log.get("event") == PROVIDER_PROBE_MISS
        and log.get("reason") == "unexpected_error"
    ]
    assert len(misses) == 1
    record = misses[0]
    # No traceback frame-locals reach the sink.
    assert "exc_info" not in record
    assert record["log_level"] == "warning"
    # Scrubbed, typed error context is present instead.
    assert record["error_type"] == "RuntimeError"
    assert "error" in record
    # The seeded secret in the exception message must not survive
    # ``safe_error_description`` scrubbing into the structured field.
    assert "topsecret999" not in str(record["error"])
    # URL stays redacted (userinfo + query stripped).
    assert record["url"] == redact_url(_CRED_URL)
    assert "topsecret999" not in str(record["url"])
