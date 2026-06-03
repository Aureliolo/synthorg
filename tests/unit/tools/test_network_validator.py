"""Unit tests for the shared network validator (SSRF prevention)."""

import asyncio
import ipaddress
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from synthorg.tools.network_validator import (
    BLOCKED_NETWORKS,
    DnsValidationOk,
    NetworkPolicy,
    check_resolved_ips,
    extract_hostname,
    is_allowed_http_scheme,
    is_blocked_ip,
    resolve_and_check,
    resolve_dns,
    validate_url_host,
)

# ── NetworkPolicy model ───────────────────────────────────────


class TestNetworkPolicy:
    """Tests for the NetworkPolicy Pydantic model."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        policy = NetworkPolicy()
        assert policy.block_private_ips is True
        assert policy.hostname_allowlist == ()
        assert policy.dns_resolution_timeout == 5.0

    @pytest.mark.unit
    def test_frozen(self) -> None:
        policy = NetworkPolicy()
        with pytest.raises(ValidationError):
            policy.block_private_ips = False  # type: ignore[misc]

    @pytest.mark.unit
    def test_allowlist_normalized_lowercase(self) -> None:
        policy = NetworkPolicy(hostname_allowlist=("Example.COM", "Test.IO"))
        assert policy.hostname_allowlist == ("example.com", "test.io")

    @pytest.mark.unit
    def test_allowlist_deduplicated(self) -> None:
        policy = NetworkPolicy(
            hostname_allowlist=("example.com", "EXAMPLE.COM", "example.com"),
        )
        assert policy.hostname_allowlist == ("example.com",)

    @pytest.mark.unit
    def test_dns_timeout_bounds(self) -> None:
        NetworkPolicy(dns_resolution_timeout=0.1)
        NetworkPolicy(dns_resolution_timeout=30.0)
        with pytest.raises(ValidationError):
            NetworkPolicy(dns_resolution_timeout=0)
        with pytest.raises(ValidationError):
            NetworkPolicy(dns_resolution_timeout=31.0)


# ── is_blocked_ip ──────────────────────────────────────────────


class TestIsBlockedIp:
    """Tests for the IP blocklist checker."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "addr",
        [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.0.1",
            "0.0.0.0",  # noqa: S104
            "::1",
            "fe80::1",
            "fc00::1",
        ],
    )
    def test_private_ips_blocked(self, addr: str) -> None:
        assert is_blocked_ip(addr) is True

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "addr",
        [
            "8.8.8.8",
            "1.1.1.1",
            "203.0.114.1",
            "2606:4700::1111",
        ],
    )
    def test_public_ips_allowed(self, addr: str) -> None:
        assert is_blocked_ip(addr) is False

    @pytest.mark.unit
    def test_ipv6_mapped_ipv4_loopback_blocked(self) -> None:
        assert is_blocked_ip("::ffff:127.0.0.1") is True

    @pytest.mark.unit
    def test_ipv6_mapped_ipv4_public_allowed(self) -> None:
        assert is_blocked_ip("::ffff:8.8.8.8") is False

    @pytest.mark.unit
    def test_unparseable_ip_blocked(self) -> None:
        assert is_blocked_ip("not-an-ip") is True


# ── extract_hostname ───────────────────────────────────────────


class TestExtractHostname:
    """Tests for hostname extraction from URLs."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://example.com/path", "example.com"),
            ("http://test.io:8080/api", "test.io"),
            ("https://[::1]/path", "::1"),
            ("http://user:pass@host.com/path", "host.com"),
        ],
    )
    def test_standard_urls(self, url: str, expected: str) -> None:
        assert extract_hostname(url) == expected

    @pytest.mark.unit
    def test_no_scheme_returns_none(self) -> None:
        assert extract_hostname("example.com/path") is None

    @pytest.mark.unit
    def test_empty_hostname_returns_none(self) -> None:
        assert extract_hostname("http:///path") is None


# ── is_allowed_http_scheme ─────────────────────────────────────


class TestIsAllowedHttpScheme:
    """Tests for HTTP scheme validation."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com",
            "https://example.com",
            "http://localhost:8080",
            "https://api.test.io/v1",
        ],
    )
    def test_allowed_schemes(self, url: str) -> None:
        assert is_allowed_http_scheme(url) is True

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://files.example.com",
            "gopher://old.server.com",
            "ssh://git@host.com",
            "-http://flag-injection",
            "javascript:alert(1)",
            "data:text/html,<h1>hi</h1>",
        ],
    )
    def test_rejected_schemes(self, url: str) -> None:
        assert is_allowed_http_scheme(url) is False


# ── check_resolved_ips ─────────────────────────────────────────


class TestCheckResolvedIps:
    """Tests for IP validation of DNS results."""

    @pytest.mark.unit
    def test_all_public_ips_pass(self) -> None:
        results = [
            (0, 0, 0, "", ("8.8.8.8", 0)),
            (0, 0, 0, "", ("8.8.4.4", 0)),
        ]
        result = check_resolved_ips("example.com", results)
        assert isinstance(result, tuple)
        assert set(result) == {"8.8.8.8", "8.8.4.4"}

    @pytest.mark.unit
    def test_private_ip_returns_error(self) -> None:
        results = [
            (0, 0, 0, "", ("8.8.8.8", 0)),
            (0, 0, 0, "", ("127.0.0.1", 0)),
        ]
        result = check_resolved_ips("evil.com", results)
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    @pytest.mark.unit
    def test_deduplicates_ips(self) -> None:
        results = [
            (0, 0, 0, "", ("8.8.8.8", 0)),
            (0, 0, 0, "", ("8.8.8.8", 0)),
        ]
        result = check_resolved_ips("example.com", results)
        assert isinstance(result, tuple)
        assert result == ("8.8.8.8",)


# ── resolve_dns ────────────────────────────────────────────────


class TestResolveDns:
    """Tests for async DNS resolution."""

    @pytest.mark.unit
    async def test_successful_resolution(self) -> None:
        mock_results = [(0, 0, 0, "", ("8.8.8.8", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await resolve_dns("example.com", 5.0)
        assert result == mock_results

    @pytest.mark.unit
    async def test_timeout_returns_error_string(self) -> None:
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.side_effect = TimeoutError()
            result = await resolve_dns("slow.com", 0.001)
        assert isinstance(result, str)
        assert "timed out" in result

    @pytest.mark.unit
    async def test_os_error_returns_error_string(self) -> None:
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.side_effect = OSError("name resolution failed")
            result = await resolve_dns("bad.com", 5.0)
        assert isinstance(result, str)
        assert "failed" in result

    @pytest.mark.unit
    async def test_empty_results_returns_error(self) -> None:
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = []
            result = await resolve_dns("empty.com", 5.0)
        assert isinstance(result, str)
        assert "no results" in result


# ── resolve_and_check ──────────────────────────────────────────


class TestResolveAndCheck:
    """Tests for combined DNS resolve + IP check."""

    @pytest.mark.unit
    async def test_public_ip_passes(self) -> None:
        mock_results = [(0, 0, 0, "", ("8.8.8.8", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await resolve_and_check("example.com", 5.0)
        assert isinstance(result, tuple)
        assert result == ("8.8.8.8",)

    @pytest.mark.unit
    async def test_private_ip_blocked(self) -> None:
        mock_results = [(0, 0, 0, "", ("192.168.1.1", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await resolve_and_check("internal.com", 5.0)
        assert isinstance(result, str)
        assert "blocked" in result.lower()


# ── validate_url_host ──────────────────────────────────────────


class TestValidateUrlHost:
    """Tests for the main URL host validation function."""

    @pytest.mark.unit
    async def test_public_url_allowed(self) -> None:
        policy = NetworkPolicy()
        mock_results = [(0, 0, 0, "", ("93.184.216.34", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host("https://example.com/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "example.com"
        assert result.port == 443
        assert result.is_https is True

    @pytest.mark.unit
    async def test_http_url_port_80(self) -> None:
        policy = NetworkPolicy()
        mock_results = [(0, 0, 0, "", ("93.184.216.34", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host("http://example.com/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.port == 80
        assert result.is_https is False

    @pytest.mark.unit
    async def test_literal_private_ip_blocked(self) -> None:
        policy = NetworkPolicy()
        result = await validate_url_host("http://127.0.0.1/admin", policy)
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    @pytest.mark.unit
    async def test_literal_public_ip_allowed(self) -> None:
        policy = NetworkPolicy()
        result = await validate_url_host("http://8.8.8.8/dns", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "8.8.8.8"

    @pytest.mark.unit
    async def test_allowlisted_host_bypasses_check(self) -> None:
        policy = NetworkPolicy(hostname_allowlist=("internal.corp",))
        result = await validate_url_host("https://internal.corp/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "internal.corp"

    @pytest.mark.unit
    async def test_block_private_ips_disabled(self) -> None:
        policy = NetworkPolicy(block_private_ips=False)
        result = await validate_url_host("http://192.168.1.1/admin", policy)
        assert isinstance(result, DnsValidationOk)

    @pytest.mark.unit
    async def test_no_hostname_returns_error(self) -> None:
        policy = NetworkPolicy()
        result = await validate_url_host("not-a-url", policy)
        assert isinstance(result, str)
        assert "hostname" in result.lower()

    @pytest.mark.unit
    async def test_dns_resolving_to_private_blocked(self) -> None:
        policy = NetworkPolicy()
        mock_results = [(0, 0, 0, "", ("10.0.0.1", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host("https://evil.com/steal", policy)
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    @pytest.mark.unit
    async def test_custom_port_preserved(self) -> None:
        policy = NetworkPolicy()
        mock_results = [(0, 0, 0, "", ("93.184.216.34", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host("https://example.com:8443/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.port == 8443

    @pytest.mark.unit
    async def test_resolved_ips_carried_in_result(self) -> None:
        policy = NetworkPolicy()
        mock_results = [
            (0, 0, 0, "", ("93.184.216.34", 0)),
            (0, 0, 0, "", ("93.184.216.35", 0)),
        ]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host("https://example.com/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert set(result.resolved_ips) == {"93.184.216.34", "93.184.216.35"}


# ── BLOCKED_NETWORKS constant ──────────────────────────────────


class TestBlockedNetworks:
    """Tests for the shared blocklist constant."""

    @pytest.mark.unit
    def test_is_non_empty_tuple(self) -> None:
        assert isinstance(BLOCKED_NETWORKS, tuple)
        assert len(BLOCKED_NETWORKS) > 0

    @pytest.mark.unit
    def test_contains_ipv4_and_ipv6(self) -> None:
        has_v4 = any(isinstance(n, ipaddress.IPv4Network) for n in BLOCKED_NETWORKS)
        has_v6 = any(isinstance(n, ipaddress.IPv6Network) for n in BLOCKED_NETWORKS)
        assert has_v4
        assert has_v6


class TestNetworkPolicyBeforeValidatorImmutability:
    """The allowlist before-validator must not mutate caller input."""

    @pytest.mark.unit
    def test_normalize_does_not_mutate_input(self) -> None:
        import copy

        original = {
            "hostname_allowlist": ["Example.COM", "Test.IO", "example.com"],
            "block_private_ips": True,
        }
        snapshot = copy.deepcopy(original)
        policy = NetworkPolicy.model_validate(original)

        assert original == snapshot, "before-validator mutated caller input"
        assert policy.hostname_allowlist == ("example.com", "test.io")

    @pytest.mark.unit
    def test_input_dict_remains_reusable(self) -> None:
        original = {"hostname_allowlist": ("Example.COM", "Test.IO")}
        first = NetworkPolicy.model_validate(original)
        second = NetworkPolicy.model_validate(original)
        assert first.hostname_allowlist == second.hostname_allowlist
        assert original == {"hostname_allowlist": ("Example.COM", "Test.IO")}
