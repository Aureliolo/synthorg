"""Unit tests for the shared network validator (SSRF prevention)."""

import asyncio
import copy
import ipaddress
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from synthorg.tools.network_validator import (
    BLOCKED_NETWORKS,
    DnsValidationOk,
    NetworkPolicy,
    SsrfValidator,
    UrlHostValidator,
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
    def test_allowlist_unicode_entry_stored_as_a_label(self) -> None:
        policy = NetworkPolicy(hostname_allowlist=("exämple.com",))
        assert policy.hostname_allowlist == ("xn--exmple-cua.com",)

    @pytest.mark.unit
    def test_allowlist_underscore_host_survives(self) -> None:
        """STD3 rules must not reach a host an operator can legitimately run."""
        policy = NetworkPolicy(hostname_allowlist=("my_host.internal",))
        assert policy.hostname_allowlist == ("my_host.internal",)

    @pytest.mark.unit
    def test_allowlist_rejects_unresolvable_a_label(self) -> None:
        with pytest.raises(ValidationError):
            NetworkPolicy(hostname_allowlist=("xn--bogus-.com",))

    @pytest.mark.unit
    def test_allowlist_rejects_a_non_string_entry_as_a_validation_error(self) -> None:
        """The refusal has to stay inside the hierarchy both guards read.

        This validator runs before field validation, so a non-string element
        reaches ``str`` methods. Raising ``AttributeError`` there would leave
        Pydantic unable to convert it and the startup-path caller unable to
        catch it, so the process would go down on a persisted value.
        """
        with pytest.raises(ValidationError):
            NetworkPolicy(hostname_allowlist=(123,))  # type: ignore[arg-type]

    @pytest.mark.unit
    @pytest.mark.parametrize("wrap", [set, frozenset])
    def test_allowlist_normalises_every_collection_pydantic_accepts(
        self,
        wrap: type[frozenset[str]] | type[set[str]],
    ) -> None:
        """A form that skips normalisation is an entry nothing can match.

        Pydantic fills a tuple field from any collection, so an unordered one
        reaching the field unnormalised would sit there uppercased and as a
        U-label while the request side arrives lowercased and as an A-label.
        """
        policy = NetworkPolicy(
            hostname_allowlist=wrap({"ExÄmple.COM"}),  # type: ignore[arg-type]
        )
        assert policy.hostname_allowlist == ("xn--exmple-cua.com",)

    @pytest.mark.unit
    def test_allowlist_rejects_a_mapping(self) -> None:
        """Iterating a mapping would allowlist its keys.

        Pydantic refuses a mapping for this field on its own, so admitting one
        here would widen what the field accepts rather than normalise it.
        """
        with pytest.raises(ValidationError):
            NetworkPolicy(
                hostname_allowlist={"internal.corp": True},  # type: ignore[arg-type]
            )

    @pytest.mark.unit
    def test_allowlist_rejects_a_form_it_cannot_normalise(self) -> None:
        """An iterator is consumed by reading it, so it is refused, not passed.

        Letting it through would hand Pydantic an unnormalised allowlist,
        which is a silent bypass of every check this validator applies.
        """
        with pytest.raises(ValidationError):
            NetworkPolicy(
                hostname_allowlist=iter(["example.com"]),  # type: ignore[arg-type]
            )

    @pytest.mark.unit
    def test_allowlist_collapses_alternate_spellings_of_one_host(self) -> None:
        """Canonicalising must happen before the dedupe, not after it.

        Reordering the two would store the same host several times and let
        each spelling drift out of agreement with the request side.
        """
        policy = NetworkPolicy(
            hostname_allowlist=(
                "example.com",
                "exämple.com",
                "XN--exmple-cua.com",
                "Exämple.COM",
            ),
        )
        assert policy.hostname_allowlist == ("example.com", "xn--exmple-cua.com")

    @pytest.mark.unit
    def test_allowlist_keeps_a_mixed_label_host(self) -> None:
        policy = NetworkPolicy(hostname_allowlist=("my_service.xn--mnchen-3ya.de",))
        assert policy.hostname_allowlist == ("my_service.xn--mnchen-3ya.de",)

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
    async def test_unicode_host_matches_its_a_label_allowlist_entry(self) -> None:
        # The allowlist branch still resolves, to pin the IP, so this needs the
        # resolver stubbed: unmocked it reaches real DNS from the unit suite,
        # and the branch tolerates a lookup failure, so the test would pass
        # either way and say nothing about what it claims to check.
        policy = NetworkPolicy(hostname_allowlist=("xn--exmple-cua.com",))
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = [(0, 0, 0, "", ("93.184.216.34", 0))]
            result = await validate_url_host("https://exämple.com/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "xn--exmple-cua.com"
        assert result.resolved_ips == ("93.184.216.34",)

    @pytest.mark.unit
    async def test_invalid_a_label_host_refused(self) -> None:
        policy = NetworkPolicy()
        result = await validate_url_host("https://xn--bogus-.com/api", policy)
        assert result == (
            "Hostname is not a valid internationalised domain name: invalid_alabel"
        )

    @pytest.mark.unit
    async def test_invalid_a_label_refused_with_blocking_disabled(self) -> None:
        """Dev-mode "allow everything" must not also disable the IDNA guard.

        The canonicalisation runs before the master switch, so a refactor
        that moved it below would send a spoofed hostname straight to DNS
        for every operator running with private-IP blocking off.
        """
        policy = NetworkPolicy(block_private_ips=False)
        result = await validate_url_host("https://xn--bogus-.com/api", policy)
        assert isinstance(result, str)
        assert "invalid_alabel" in result

    @pytest.mark.unit
    async def test_invalid_a_label_refused_even_when_allowlisted(self) -> None:
        policy = NetworkPolicy(hostname_allowlist=("example.com",))
        result = await validate_url_host("https://xn--bogus-.com/api", policy)
        assert isinstance(result, str)
        assert "invalid_alabel" in result

    @pytest.mark.unit
    async def test_sibling_label_does_not_veto_its_neighbours(self) -> None:
        """An underscore label beside an A-label must not refuse the host."""
        policy = NetworkPolicy()
        mock_results = [(0, 0, 0, "", ("93.184.216.34", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host(
                "https://my_service.xn--mnchen-3ya.de/api", policy
            )
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "my_service.xn--mnchen-3ya.de"

    @pytest.mark.unit
    @pytest.mark.parametrize("suffix", ["\x00", "\x00.evil.example", " "])
    async def test_control_character_host_refused(self, suffix: str) -> None:
        """The resolver truncates at a NUL; nothing above it does.

        ``\\n``, ``\\r`` and ``\\t`` are absent from this list on purpose:
        ``urlparse`` strips them before returning a hostname, so they never
        reach the guard and asserting on them would pin the standard
        library's behaviour rather than this function's.
        """
        policy = NetworkPolicy()
        result = await validate_url_host(f"https://example.com{suffix}/api", policy)
        # Every refusal is a string, a failed DNS lookup included, so asserting
        # the type alone would pass on the outcome this test exists to rule
        # out: the hostname reaching a real resolver.
        assert result == "Could not extract a hostname from the URL"

    @pytest.mark.unit
    async def test_underscore_host_still_reaches_dns(self) -> None:
        """The guard must not start refusing hosts it has always allowed."""
        policy = NetworkPolicy()
        mock_results = [(0, 0, 0, "", ("93.184.216.34", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host("https://my_host.example/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "my_host.example"

    @pytest.mark.unit
    async def test_allowlisted_host_bypasses_check(self) -> None:
        """An allowlisted host still resolves, so the IP can be pinned.

        The lookup is mocked because a unit test must not depend on what a
        real resolver answers for ``internal.corp``.
        """
        policy = NetworkPolicy(hostname_allowlist=("internal.corp",))
        mock_results = [(0, 0, 0, "", ("93.184.216.34", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host("https://internal.corp/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "internal.corp"
        assert result.resolved_ips == ("93.184.216.34",)

    @pytest.mark.unit
    async def test_allowlisted_host_resolving_private_is_still_allowed(self) -> None:
        """The allowlist exists for internal addresses, so it carries none.

        A blocked address yields no pinned IPs rather than a refusal: the
        entry is the operator saying this host is legitimately internal.
        """
        policy = NetworkPolicy(hostname_allowlist=("internal.corp",))
        mock_results = [(0, 0, 0, "", ("10.0.0.5", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validate_url_host("https://internal.corp/api", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == ()

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


# ── SsrfValidator seam ─────────────────────────────────────────


class TestUrlHostValidator:
    """Tests for the SsrfValidator protocol + UrlHostValidator adapter."""

    @pytest.mark.unit
    def test_adapter_satisfies_protocol(self) -> None:
        validator = UrlHostValidator(NetworkPolicy())
        assert isinstance(validator, SsrfValidator)

    @pytest.mark.unit
    async def test_delegates_to_validate_url_host(self) -> None:
        validator = UrlHostValidator(NetworkPolicy())
        mock_results = [(0, 0, 0, "", ("93.184.216.34", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await validator.validate("https://example.com/api")
        assert isinstance(result, DnsValidationOk)
        assert result.hostname == "example.com"

    @pytest.mark.unit
    async def test_rejects_private_target(self) -> None:
        validator = UrlHostValidator(NetworkPolicy())
        result = await validator.validate("http://127.0.0.1/admin")
        assert isinstance(result, str)


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
