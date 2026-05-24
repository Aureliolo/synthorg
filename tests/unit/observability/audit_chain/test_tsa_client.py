"""Unit tests for the RFC 3161 TSA client.

Covers:

* Happy path isn't covered here (requires a real TSA response DER
  fixture; see the opt-in integration test). These tests focus on
  error paths that can be exercised without a valid signed response.
* Transport failures: timeouts, 4xx/5xx, wrong Content-Type.
* Protocol failures: malformed ASN.1.
* Constructor validation: unknown hash algorithm, invalid timeout,
  unparseable trusted-root PEM.
"""

from typing import Any

import httpx
import pytest
import respx

from synthorg.observability.audit_chain.tsa_client import (
    TsaClient,
    TsaProtocolError,
    TsaTimeoutError,
    TsaTransportError,
)

pytestmark = pytest.mark.unit

_TSA_URL = "https://tsa.example.invalid/tsr"


# -- Constructor validation --------------------------------------------------


def test_unknown_hash_algorithm_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported hash algorithm"):
        TsaClient(_TSA_URL, hash_algorithm="md5")


def test_non_positive_timeout_rejected() -> None:
    with pytest.raises(ValueError, match="timeout_sec must be positive"):
        TsaClient(_TSA_URL, timeout_sec=0.0)


def test_unparseable_trusted_root_raises() -> None:
    with pytest.raises(ValueError, match="Invalid trusted-root PEM"):
        TsaClient(_TSA_URL, trusted_roots=(b"not-a-pem-cert",))


# -- Transport failures ------------------------------------------------------


@pytest.mark.parametrize(
    ("mock_kwargs", "expected_error"),
    [
        pytest.param(
            {"side_effect": httpx.TimeoutException("slow")},
            TsaTimeoutError,
            id="timeout",
        ),
        pytest.param(
            {
                "return_value": httpx.Response(
                    400,
                    content=b"bad request",
                    headers={"Content-Type": "application/timestamp-reply"},
                ),
            },
            TsaTransportError,
            id="http_4xx",
        ),
        pytest.param(
            {
                "return_value": httpx.Response(
                    503,
                    content=b"down",
                    headers={"Content-Type": "application/timestamp-reply"},
                ),
            },
            TsaTransportError,
            id="http_5xx",
        ),
        pytest.param(
            {"side_effect": httpx.ConnectError("dns fail")},
            TsaTransportError,
            id="connect_error",
        ),
    ],
)
async def test_transport_failures_map_to_typed_errors(
    mock_kwargs: dict[str, Any],  # type: ignore[explicit-any]  # respx.MockRouter.post kwargs include status_code, content, side_effect with heterogeneous types
    expected_error: type[Exception],
) -> None:
    async with respx.mock(base_url=_TSA_URL) as router:
        router.post("").mock(**mock_kwargs)
        client = TsaClient(_TSA_URL, timeout_sec=0.5)
        with pytest.raises(expected_error):
            await client.request_timestamp(b"payload")
        await client.aclose()


async def test_wrong_content_type_rejected() -> None:
    """A 200 response with the wrong Content-Type is treated as a protocol error."""
    async with respx.mock(base_url=_TSA_URL) as router:
        router.post("").mock(
            return_value=httpx.Response(
                200,
                content=b"<html>oops, proxy error page</html>",
                headers={"Content-Type": "text/html"},
            )
        )
        client = TsaClient(_TSA_URL, timeout_sec=0.5)
        with pytest.raises(TsaProtocolError, match="Content-Type"):
            await client.request_timestamp(b"payload")
        await client.aclose()


# -- Protocol failures -------------------------------------------------------


async def test_malformed_asn1_response_raises_protocol_error() -> None:
    async with respx.mock(base_url=_TSA_URL) as router:
        router.post("").mock(
            return_value=httpx.Response(
                200,
                content=b"\x00\x01\x02\x03\x04not-asn1",
                headers={"Content-Type": "application/timestamp-reply"},
            )
        )
        client = TsaClient(_TSA_URL, timeout_sec=0.5)
        with pytest.raises(TsaProtocolError, match=r"not a valid ASN\.1"):
            await client.request_timestamp(b"payload")
        await client.aclose()


# -- Shared http_client ownership -------------------------------------------


async def test_injected_http_client_not_closed_by_aclose() -> None:
    shared = httpx.AsyncClient()
    try:
        client = TsaClient(_TSA_URL, http_client=shared)
        await client.aclose()
        # Shared client must remain open for the caller's lifetime.
        assert not shared.is_closed
    finally:
        await shared.aclose()
