"""Tests for TOCTOU DNS rebinding mitigation in git clone validation."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from synthorg.tools.git_url_validator import (
    DnsValidationOk,
    build_curl_resolve_value,
    verify_dns_consistency,
)

from .conftest import dns_result

# ── DnsValidationOk ──────────────────────────────────────────────


@pytest.mark.unit
class TestDnsValidationOk:
    """Successful DNS validation result model."""

    def test_defaults(self) -> None:
        result = DnsValidationOk(hostname="example.com")
        assert result.hostname == "example.com"
        assert result.port is None
        assert result.resolved_ips == ()
        assert result.is_https is False

    def test_full_construction(self) -> None:
        result = DnsValidationOk(
            hostname="example.com",
            port=443,
            resolved_ips=("93.184.216.34", "1.2.3.4"),
            is_https=True,
        )
        assert result.hostname == "example.com"
        assert result.port == 443
        assert result.resolved_ips == ("93.184.216.34", "1.2.3.4")
        assert result.is_https is True

    def test_frozen(self) -> None:
        result = DnsValidationOk(hostname="example.com")
        with pytest.raises(ValidationError):
            result.hostname = "other.com"  # type: ignore[misc]

    def test_rejects_blank_hostname(self) -> None:
        """NotBlankStr rejects empty and whitespace-only hostnames."""
        with pytest.raises(ValidationError):
            DnsValidationOk(hostname="")
        with pytest.raises(ValidationError):
            DnsValidationOk(hostname="   ")

    def test_rejects_invalid_port(self) -> None:
        """Port must be > 0 and <= 65535."""
        with pytest.raises(ValidationError):
            DnsValidationOk(hostname="example.com", port=0)
        with pytest.raises(ValidationError):
            DnsValidationOk(hostname="example.com", port=70000)


# ── build_curl_resolve_value ─────────────────────────────────────


@pytest.mark.unit
class TestBuildCurlResolveValue:
    """curloptResolve config value construction."""

    def test_single_ipv4(self) -> None:
        val = build_curl_resolve_value("example.com", 443, ("93.184.216.34",))
        assert val == "example.com:443:93.184.216.34"

    def test_multiple_ipv4(self) -> None:
        val = build_curl_resolve_value(
            "example.com",
            443,
            ("93.184.216.34", "1.2.3.4"),
        )
        assert val == "example.com:443:93.184.216.34,1.2.3.4"

    def test_ipv6_bracketed(self) -> None:
        """IPv6 addresses in curloptResolve are wrapped in brackets."""
        val = build_curl_resolve_value(
            "example.com",
            443,
            ("2607:f8b0:4004:800::200e",),
        )
        assert val == "example.com:443:[2607:f8b0:4004:800::200e]"

    def test_mixed_ipv4_ipv6(self) -> None:
        val = build_curl_resolve_value(
            "example.com",
            443,
            ("93.184.216.34", "2607:f8b0:4004:800::200e"),
        )
        assert val == "example.com:443:93.184.216.34,[2607:f8b0:4004:800::200e]"

    def test_custom_port(self) -> None:
        val = build_curl_resolve_value("example.com", 8443, ("1.2.3.4",))
        assert val == "example.com:8443:1.2.3.4"

    def test_empty_ips_raises(self) -> None:
        """Empty ips tuple raises ValueError."""
        with pytest.raises(ValueError, match="ips must not be empty"):
            build_curl_resolve_value("example.com", 443, ())


# ── verify_dns_consistency ────────────────────────────────────────


@pytest.mark.unit
class TestVerifyDnsConsistency:
    """Double-resolve consistency check for SSH/SCP URLs."""

    async def test_same_ips_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same IPs on re-resolve returns None (consistent)."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=dns_result("93.184.216.34")),
        )
        result = await verify_dns_consistency(
            "example.com",
            frozenset({"93.184.216.34"}),
            dns_timeout=5.0,
        )
        assert result is None

    async def test_subset_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-resolved subset of original IPs passes."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=dns_result("93.184.216.34")),
        )
        result = await verify_dns_consistency(
            "example.com",
            frozenset({"93.184.216.34", "1.2.3.4"}),
            dns_timeout=5.0,
        )
        assert result is None

    async def test_new_public_ip_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """New public IP not in original set is flagged as rebinding."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=dns_result("5.6.7.8")),
        )
        result = await verify_dns_consistency(
            "example.com",
            frozenset({"93.184.216.34"}),
            dns_timeout=5.0,
        )
        assert result is not None
        assert "rebinding" in result.lower()

    async def test_private_ip_on_reresolution_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Private IP on re-resolution is blocked (primary defense)."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=dns_result("127.0.0.1")),
        )
        result = await verify_dns_consistency(
            "example.com",
            frozenset({"93.184.216.34"}),
            dns_timeout=5.0,
        )
        assert result is not None
        assert "blocked" in result.lower()

    async def test_dns_failure_on_reresolution_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DNS failure on re-resolution is fail-closed."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, side_effect=OSError("DNS failed")),
        )
        result = await verify_dns_consistency(
            "example.com",
            frozenset({"93.184.216.34"}),
            dns_timeout=5.0,
        )
        assert result is not None
        assert "failed" in result.lower()

    async def test_dns_timeout_on_reresolution_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DNS timeout on re-resolution is fail-closed."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, side_effect=TimeoutError("DNS timed out")),
        )
        result = await verify_dns_consistency(
            "example.com",
            frozenset({"93.184.216.34"}),
            dns_timeout=0.001,
        )
        assert result is not None
        assert "timed out" in result.lower()
