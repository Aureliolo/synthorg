"""Tests for git clone URL validation and SSRF prevention."""

import asyncio
import ipaddress
from unittest.mock import AsyncMock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.tools.git_url_validator import (
    _BLOCKED_NETWORKS,
    DnsValidationOk,
    GitCloneNetworkPolicy,
    _extract_hostname,
    _is_blocked_ip,
    is_allowed_clone_scheme,
    validate_clone_url_host,
)

from .conftest import dns_result as _dns_result
from .conftest import dns_result_v6 as _dns_result_v6

# ── _extract_hostname ─────────────────────────────────────────────


@pytest.mark.unit
class TestExtractHostname:
    """Hostname extraction from various URL formats."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://github.com/user/repo.git", "github.com"),
            ("https://HOST.EXAMPLE.COM/repo", "host.example.com"),
            ("https://host:8443/repo.git", "host"),
            ("https://user@host/repo.git", "host"),
            ("ssh://git@host.example/repo", "host.example"),
            ("ssh://git@host:22/repo.git", "host"),
            ("https://[::1]/repo.git", "::1"),
            ("https://[2001:db8::1]:443/repo", "2001:db8::1"),
        ],
        ids=[
            "https-basic",
            "https-uppercase",
            "https-port",
            "https-userinfo",
            "ssh-basic",
            "ssh-port",
            "https-ipv6-literal",
            "https-ipv6-port",
        ],
    )
    def test_standard_urls(self, url: str, expected: str) -> None:
        assert _extract_hostname(url) == expected

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("git@github.com:user/repo.git", "github.com"),
            ("deploy@host.example:path/repo", "host.example"),
            ("git@[::1]:repo.git", "::1"),
        ],
        ids=["scp-basic", "scp-custom-user", "scp-ipv6"],
    )
    def test_scp_like(self, url: str, expected: str) -> None:
        assert _extract_hostname(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not-a-url",
            "/local/path",
            "https://",
            "https:///path",
            "git@[]:repo.git",
            "git@[::1:repo.git",
        ],
        ids=[
            "empty",
            "bare-string",
            "local-path",
            "scheme-only",
            "empty-host",
            "scp-empty-brackets",
            "scp-unclosed-bracket",
        ],
    )
    def test_unparseable_returns_none(self, url: str) -> None:
        assert _extract_hostname(url) is None


# ── _is_blocked_ip ────────────────────────────────────────────────


@pytest.mark.unit
class TestIsBlockedIp:
    """Private/reserved IP detection including IPv6-mapped IPv4."""

    @pytest.mark.parametrize(
        "addr",
        [
            "127.0.0.1",
            "127.255.255.255",
            "10.0.0.1",
            "10.255.255.255",
            "100.64.0.1",
            "100.127.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.0.0.1",
            "192.0.2.1",
            "192.168.0.1",
            "192.168.255.255",
            "198.18.0.1",
            "198.51.100.1",
            "203.0.113.1",
            "169.254.1.1",
            "224.0.0.1",
            "240.0.0.1",
            "255.255.255.255",
            "0.0.0.0",  # noqa: S104
            "::1",
            "fe80::1",
            "fc00::1",
            "fd00::1",
            "::",
            "2001::1",
            "2001:db8::1",
            "2002:7f00:1::1",
            "2002:c0a8:101::",
            "64:ff9b::1",
            "100::1",
            "ff02::1",
            "ff05::2",
        ],
        ids=[
            "loopback-start",
            "loopback-end",
            "private-10-start",
            "private-10-end",
            "cgnat-start",
            "cgnat-end",
            "private-172-start",
            "private-172-end",
            "ietf-protocol",
            "test-net-1",
            "private-192-start",
            "private-192-end",
            "benchmarking",
            "test-net-2",
            "test-net-3",
            "link-local",
            "multicast-v4",
            "reserved",
            "broadcast",
            "unspecified-v4",
            "loopback-v6",
            "link-local-v6",
            "ula-v6-fc",
            "ula-v6-fd",
            "unspecified-v6",
            "teredo",
            "documentation-v6",
            "6to4-loopback",
            "6to4-private",
            "nat64",
            "discard-v6",
            "multicast-v6-link",
            "multicast-v6-site",
        ],
    )
    def test_blocked_addresses(self, addr: str) -> None:
        assert _is_blocked_ip(addr) is True

    @pytest.mark.parametrize(
        "addr",
        [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",
            "2607:f8b0:4004:800::200e",
        ],
        ids=[
            "google-dns",
            "cloudflare-dns",
            "example-com",
            "google-v6",
        ],
    )
    def test_public_addresses(self, addr: str) -> None:
        assert _is_blocked_ip(addr) is False

    @pytest.mark.parametrize(
        ("mapped", "expected"),
        [
            ("::ffff:127.0.0.1", True),
            ("::ffff:10.0.0.1", True),
            ("::ffff:192.168.1.1", True),
            ("::ffff:8.8.8.8", False),
            ("::ffff:93.184.216.34", False),
        ],
        ids=[
            "mapped-loopback",
            "mapped-private-10",
            "mapped-private-192",
            "mapped-google-dns",
            "mapped-example-com",
        ],
    )
    def test_ipv6_mapped_ipv4(self, mapped: str, expected: bool) -> None:
        assert _is_blocked_ip(mapped) is expected

    def test_unparseable_is_blocked(self) -> None:
        """Unparseable addresses are blocked (fail-closed)."""
        assert _is_blocked_ip("not-an-ip") is True


# ── is_allowed_clone_scheme ───────────────────────────────────────


@pytest.mark.unit
class TestIsAllowedCloneScheme:
    """Scheme validation for clone URLs (moved from test_git_tools)."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/user/repo.git",
            "https://host:8443/repo.git",
            "ssh://git@host/repo.git",
            "ssh://host:22/repo.git",
            "git@github.com:user/repo.git",
            "deploy@host.example:path/repo",
            "git@[::1]:repo.git",
            "git@[2001:db8::1]:repo.git",
        ],
        ids=[
            "https",
            "https-port",
            "ssh",
            "ssh-port",
            "scp-git",
            "scp-deploy",
            "scp-ipv6-loopback",
            "scp-ipv6-documentation",
        ],
    )
    def test_allowed_schemes(self, url: str) -> None:
        assert is_allowed_clone_scheme(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "/etc/passwd",
            "file:///etc",
            "ext::sh -c 'evil'",
            "../outside-repo",
            "-cfoo=bar@host:path",
            "http://insecure.example.com/repo",
            "not-a-real-url-at-all",
        ],
        ids=[
            "local-path",
            "file-scheme",
            "ext-protocol",
            "relative-path",
            "flag-injection",
            "http-insecure",
            "garbage",
        ],
    )
    def test_blocked_schemes(self, url: str) -> None:
        assert is_allowed_clone_scheme(url) is False


# ── GitCloneNetworkPolicy ────────────────────────────────────────


@pytest.mark.unit
class TestGitCloneNetworkPolicy:
    """Pydantic model defaults, bounds, and immutability."""

    def test_defaults(self) -> None:
        policy = GitCloneNetworkPolicy()
        assert policy.hostname_allowlist == ()
        assert policy.block_private_ips is True
        assert policy.dns_resolution_timeout == 5.0
        assert policy.dns_rebinding_mitigation is True

    def test_custom_values(self) -> None:
        policy = GitCloneNetworkPolicy(
            hostname_allowlist=("git.internal",),
            block_private_ips=False,
            dns_resolution_timeout=10.0,
        )
        assert policy.hostname_allowlist == ("git.internal",)
        assert policy.block_private_ips is False
        assert policy.dns_resolution_timeout == 10.0

    def test_frozen(self) -> None:
        policy = GitCloneNetworkPolicy()
        with pytest.raises(ValidationError):
            policy.block_private_ips = False  # type: ignore[misc]

    def test_timeout_bounds(self) -> None:
        with pytest.raises(ValidationError):
            GitCloneNetworkPolicy(dns_resolution_timeout=0)
        with pytest.raises(ValidationError):
            GitCloneNetworkPolicy(dns_resolution_timeout=31)

    def test_timeout_rejects_inf_and_nan(self) -> None:
        """Infinity and NaN are rejected by allow_inf_nan=False."""
        with pytest.raises(ValidationError):
            GitCloneNetworkPolicy(dns_resolution_timeout=float("inf"))
        with pytest.raises(ValidationError):
            GitCloneNetworkPolicy(dns_resolution_timeout=float("nan"))

    def test_allowlist_normalized_to_lowercase(self) -> None:
        """Entries are lowercased at construction."""
        policy = GitCloneNetworkPolicy(
            hostname_allowlist=("Git.INTERNAL.Corp",),
        )
        assert policy.hostname_allowlist == ("git.internal.corp",)

    def test_allowlist_deduplicates(self) -> None:
        """Duplicate entries (case-insensitive) are removed."""
        policy = GitCloneNetworkPolicy(
            hostname_allowlist=(
                "git.internal",
                "GIT.INTERNAL",
                "git.other",
            ),
        )
        assert policy.hostname_allowlist == (
            "git.internal",
            "git.other",
        )

    def test_allowlist_rejects_empty_string(self) -> None:
        """Empty string in allowlist is rejected (NotBlankStr)."""
        with pytest.raises(ValidationError):
            GitCloneNetworkPolicy(hostname_allowlist=("",))

    def test_allowlist_rejects_whitespace(self) -> None:
        """Whitespace-only allowlist entry is rejected."""
        with pytest.raises(ValidationError):
            GitCloneNetworkPolicy(hostname_allowlist=("   ",))

    def test_dns_rebinding_mitigation_disableable(self) -> None:
        """dns_rebinding_mitigation can be disabled."""
        policy = GitCloneNetworkPolicy(dns_rebinding_mitigation=False)
        assert policy.dns_rebinding_mitigation is False


# ── validate_clone_url_host ───────────────────────────────────────


@pytest.mark.unit
class TestValidateCloneUrlHost:
    """Async SSRF validation with mocked DNS."""

    async def test_public_host_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Public host resolving to public IP returns DnsValidationOk."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=_dns_result("93.184.216.34")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("https://example.com/repo.git", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "example.com"
        assert result.resolved_ips == ("93.184.216.34",)
        assert result.is_https is True
        assert result.port == 443

    async def test_public_host_deduplicates_ips(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Duplicate IPs from getaddrinfo are deduplicated."""
        loop = asyncio.get_running_loop()
        # getaddrinfo often returns same IP for STREAM + DGRAM
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(
                spec=loop.getaddrinfo,
                return_value=_dns_result("93.184.216.34", "93.184.216.34"),
            ),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("https://example.com/repo.git", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == ("93.184.216.34",)

    async def test_https_custom_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTPS URL with custom port extracts that port."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=_dns_result("93.184.216.34")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://example.com:8443/repo.git", policy
        )
        assert isinstance(result, DnsValidationOk)
        assert result.port == 8443

    async def test_ssh_url_not_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SSH URL is not marked as HTTPS."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=_dns_result("93.184.216.34")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("ssh://git@example.com/repo.git", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.is_https is False
        assert result.port is None

    async def test_scp_url_not_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SCP-like URL is not marked as HTTPS."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=_dns_result("93.184.216.34")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("git@example.com:repo.git", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.is_https is False

    async def test_dns_rebinding_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Host resolving to private IP is blocked."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=_dns_result("127.0.0.1")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://evil.example.com/repo.git", policy
        )
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_literal_private_ip_blocked(self) -> None:
        """Literal private IP in URL is blocked (no DNS needed)."""
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://169.254.169.254/latest/meta-data", policy
        )
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_literal_public_ip_allowed(self) -> None:
        """Literal public IP returns DnsValidationOk with empty resolved_ips."""
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("https://93.184.216.34/repo.git", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == ()

    async def test_literal_ipv6_loopback_blocked(self) -> None:
        """IPv6 loopback literal in URL is blocked."""
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("https://[::1]/repo.git", policy)
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_literal_ipv6_link_local_blocked(self) -> None:
        """IPv6 link-local literal in URL is blocked."""
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("https://[fe80::1]/repo.git", policy)
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_literal_ipv6_mapped_loopback_blocked(
        self,
    ) -> None:
        """IPv6-mapped loopback literal is blocked."""
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://[::ffff:127.0.0.1]/repo.git", policy
        )
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_scp_literal_private_ip_blocked(self) -> None:
        """SCP-like URL with literal private IP is blocked."""
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("git@10.0.0.1:repo.git", policy)
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_scp_literal_loopback_blocked(self) -> None:
        """SCP-like URL with loopback IP is blocked."""
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("git@127.0.0.1:repo.git", policy)
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_allowlisted_host_bypasses_check(self) -> None:
        """Allowlisted host returns DnsValidationOk with empty resolved_ips."""
        policy = GitCloneNetworkPolicy(
            hostname_allowlist=("git.internal.corp",),
        )
        # No DNS mock needed -- allowlist check returns early
        result = await validate_clone_url_host(
            "https://git.internal.corp/repo.git", policy
        )
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == ()

    async def test_allowlist_case_insensitive(self) -> None:
        """Allowlist matching is case-insensitive."""
        policy = GitCloneNetworkPolicy(
            hostname_allowlist=("Git.Internal.Corp",),
        )
        result = await validate_clone_url_host(
            "https://git.internal.corp/repo.git", policy
        )
        assert isinstance(result, DnsValidationOk)

    async def test_dns_timeout_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DNS timeout rejects the URL (fail-closed)."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, side_effect=TimeoutError("DNS timeout")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://slow.example.com/repo.git", policy
        )
        assert isinstance(result, str)
        assert "timed out" in result.lower()

    async def test_dns_nxdomain_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DNS NXDOMAIN rejects the URL (fail-closed)."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(
                spec=loop.getaddrinfo, side_effect=OSError("Name or service not known")
            ),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://nxdomain.invalid/repo.git", policy
        )
        assert isinstance(result, str)
        assert "failed" in result.lower()

    async def test_dns_unexpected_exception_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected DNS exception rejects (fail-closed)."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, side_effect=RuntimeError("unexpected")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://broken.example.com/repo.git", policy
        )
        assert isinstance(result, str)
        assert "failed" in result.lower()

    async def test_dns_empty_results_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty DNS results reject the URL (fail-closed)."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=[]),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://empty-dns.example.com/repo.git", policy
        )
        assert isinstance(result, str)
        assert "no results" in result.lower()

    async def test_block_private_ips_disabled(self) -> None:
        """Disabling block_private_ips returns DnsValidationOk."""
        policy = GitCloneNetworkPolicy(block_private_ips=False)
        result = await validate_clone_url_host("https://127.0.0.1/repo.git", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == ()

    async def test_mixed_dns_results_one_private_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One public + one private result -> blocked."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(
                spec=loop.getaddrinfo,
                return_value=_dns_result("93.184.216.34", "127.0.0.1"),
            ),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://multi.example.com/repo.git", policy
        )
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_scp_like_private_host_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SCP-like URL to private host is blocked."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=_dns_result("10.0.0.5")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("git@internal-host:repo.git", policy)
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_unparseable_url_blocked(self) -> None:
        """URL with no extractable hostname is blocked."""
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host("not-a-url", policy)
        assert isinstance(result, str)
        assert "could not extract" in result.lower()

    async def test_ipv6_dns_result_blocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AF_INET6 DNS result with private IP is blocked."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=_dns_result_v6("::1")),
        )
        policy = GitCloneNetworkPolicy()
        result = await validate_clone_url_host(
            "https://evil-v6.example.com/repo.git", policy
        )
        assert isinstance(result, str)
        assert "blocked" in result.lower()

    async def test_mitigation_disabled_returns_empty_resolved_ips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When dns_rebinding_mitigation is off, resolved_ips is empty."""
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "getaddrinfo",
            AsyncMock(spec=loop.getaddrinfo, return_value=_dns_result("93.184.216.34")),
        )
        policy = GitCloneNetworkPolicy(dns_rebinding_mitigation=False)
        result = await validate_clone_url_host("https://example.com/repo.git", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == ()
        assert result.is_https is True


# ── Property-based tests ──────────────────────────────────────────

# Derive from production constant to prevent drift.
_ALL_BLOCKED_V4 = tuple(
    net for net in _BLOCKED_NETWORKS if isinstance(net, ipaddress.IPv4Network)
)
_ALL_BLOCKED_V6 = tuple(
    net for net in _BLOCKED_NETWORKS if isinstance(net, ipaddress.IPv6Network)
)


@pytest.mark.unit
class TestValidateCloneUrlHostProperties:
    """Hypothesis property-based tests for IP blocking."""

    @given(
        ip=st.one_of(
            *(st.ip_addresses(v=4, network=str(net)) for net in _ALL_BLOCKED_V4)
        ),
    )
    def test_blocked_ipv4_always_detected(self, ip: ipaddress.IPv4Address) -> None:
        """Every IPv4 in a blocked range is detected."""
        assert _is_blocked_ip(str(ip)) is True

    @given(
        ip=st.one_of(
            *(
                st.ip_addresses(v=6, network=str(net))
                for net in _ALL_BLOCKED_V6
                if net.num_addresses > 1  # skip ::/128 (single addr)
            )
        ),
    )
    def test_blocked_ipv6_always_detected(self, ip: ipaddress.IPv6Address) -> None:
        """Every IPv6 in a blocked range is detected."""
        assert _is_blocked_ip(str(ip)) is True

    @given(
        ip=st.ip_addresses(v=4).filter(
            lambda ip: not any(ip in net for net in _ALL_BLOCKED_V4)
        ),
    )
    def test_non_blocked_ipv4_never_flagged(self, ip: ipaddress.IPv4Address) -> None:
        """IPv4 outside blocked ranges is never flagged."""
        assert _is_blocked_ip(str(ip)) is False

    @given(
        ip=st.ip_addresses(v=6).filter(
            lambda ip: (
                not any(ip in net for net in _ALL_BLOCKED_V6)
                and not (
                    isinstance(ip, ipaddress.IPv6Address)
                    and ip.ipv4_mapped
                    and any(ip.ipv4_mapped in net for net in _ALL_BLOCKED_V4)
                )
            )
        ),
    )
    def test_non_blocked_ipv6_never_flagged(self, ip: ipaddress.IPv6Address) -> None:
        """IPv6 outside blocked ranges is never flagged.

        Excludes IPv6-mapped IPv4 addresses (``::ffff:x.x.x.x``) that
        map to blocked IPv4 ranges, since ``_is_blocked_ip`` unwraps
        these before checking.
        """
        assert _is_blocked_ip(str(ip)) is False
