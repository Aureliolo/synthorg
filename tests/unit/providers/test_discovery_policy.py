"""Tests for provider discovery SSRF allowlist policy."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.config.schema import ProviderConfig
from synthorg.providers.discovery_policy import (
    ProviderDiscoveryPolicy,
    build_seed_allowlist,
    extract_host_port,
    is_url_allowed,
    resolve_discovery_target,
    seed_from_presets,
)
from synthorg.tools.network_validator import DnsValidationOk

pytestmark = pytest.mark.unit
# ── ProviderDiscoveryPolicy model ────────────────────────────────


class TestProviderDiscoveryPolicyConstruction:
    """Construction, normalization, and immutability."""

    def test_defaults(self) -> None:
        policy = ProviderDiscoveryPolicy()
        assert policy.host_port_allowlist == ()
        assert policy.block_private_ips is True

    def test_with_entries(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("localhost:11434", "myhost:8080"),
        )
        assert policy.host_port_allowlist == ("localhost:11434", "myhost:8080")

    def test_normalizes_to_lowercase(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("HOST.EXAMPLE.COM:443", "Localhost:11434"),
        )
        assert policy.host_port_allowlist == (
            "host.example.com:443",
            "localhost:11434",
        )

    def test_deduplicates_preserving_order(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=(
                "localhost:11434",
                "myhost:8080",
                "localhost:11434",
            ),
        )
        assert policy.host_port_allowlist == ("localhost:11434", "myhost:8080")

    def test_deduplication_is_case_insensitive(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("HOST:8080", "host:8080"),
        )
        assert policy.host_port_allowlist == ("host:8080",)

    def test_frozen(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("localhost:11434",),
        )
        with pytest.raises(ValidationError):
            policy.host_port_allowlist = ("new:1234",)  # type: ignore[misc]

    def test_block_private_ips_false(self) -> None:
        policy = ProviderDiscoveryPolicy(block_private_ips=False)
        assert policy.block_private_ips is False


# ── extract_host_port ────────────────────────────────────────────


class TestExtractHostPort:
    """URL to host:port extraction."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("http://localhost:11434", "localhost:11434"),
            ("http://localhost:11434/", "localhost:11434"),
            ("http://localhost:11434/api/tags", "localhost:11434"),
            ("https://example.com:8443/v1", "example.com:8443"),
            ("http://host.docker.internal:11434", "host.docker.internal:11434"),
            ("http://172.17.0.1:1234/v1", "172.17.0.1:1234"),
            # Default ports
            ("http://example.com/v1", "example.com:80"),
            ("https://example.com/v1", "example.com:443"),
            # Uppercase normalized
            ("http://HOST.EXAMPLE.COM:8080", "host.example.com:8080"),
        ],
        ids=[
            "http-with-port",
            "trailing-slash",
            "with-path",
            "https-with-port",
            "docker-internal",
            "docker-bridge-ip",
            "http-default-port",
            "https-default-port",
            "uppercase-normalized",
        ],
    )
    def test_standard_urls(self, url: str, expected: str) -> None:
        assert extract_host_port(url) == expected

    def test_ipv6_literal(self) -> None:
        """IPv6 addresses are re-bracketed in host:port output."""
        result = extract_host_port("http://[::1]:11434/v1")
        assert result == "[::1]:11434"

    def test_no_hostname_returns_none(self) -> None:
        assert extract_host_port("not-a-url") is None

    def test_no_scheme_returns_none(self) -> None:
        assert extract_host_port("://host:8080") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_host_port("") is None

    def test_file_scheme_returns_none(self) -> None:
        assert extract_host_port("file:///etc/passwd") is None

    def test_port_zero_not_normalized_to_default(self) -> None:
        result = extract_host_port("http://host:0/v1")
        assert result == "host:0"

    def test_port_out_of_range_returns_none(self) -> None:
        """URL with port > 65535 returns None, not ValueError."""
        assert extract_host_port("http://localhost:99999/v1") is None

    def test_ftp_scheme_returns_none(self) -> None:
        """Non-HTTP schemes are rejected."""
        assert extract_host_port("ftp://host:21/path") is None


# ── seed_from_presets ────────────────────────────────────────────


class TestSeedFromPresets:
    """Seeding allowlist from preset candidate URLs."""

    def test_includes_ollama_candidates(self) -> None:
        seeds = seed_from_presets()
        assert "host.docker.internal:11434" in seeds
        assert "172.17.0.1:11434" in seeds
        assert "localhost:11434" in seeds

    def test_includes_lm_studio_candidates(self) -> None:
        seeds = seed_from_presets()
        assert "host.docker.internal:1234" in seeds
        assert "172.17.0.1:1234" in seeds
        assert "localhost:1234" in seeds

    def test_vllm_default_base_url_seeded(self) -> None:
        """vLLM has no candidate_urls but default_base_url is seeded."""
        seeds = seed_from_presets()
        assert "localhost:8000" in seeds

    def test_no_duplicates(self) -> None:
        seeds = seed_from_presets()
        assert len(seeds) == len(set(seeds))

    def test_all_entries_are_lowercase(self) -> None:
        seeds = seed_from_presets()
        for entry in seeds:
            assert entry == entry.lower()

    def test_returns_tuple(self) -> None:
        seeds = seed_from_presets()
        assert isinstance(seeds, tuple)


# ── build_seed_allowlist ─────────────────────────────────────────


class TestBuildSeedAllowlist:
    """Merge preset seeds with installed provider base_urls."""

    def test_empty_providers_returns_preset_seeds(self) -> None:
        result = build_seed_allowlist({})
        preset_seeds = seed_from_presets()
        assert set(result) == set(preset_seeds)

    def test_adds_provider_base_url(self) -> None:
        providers = {
            "custom": ProviderConfig(
                driver="litellm",
                base_url="http://my-server:9090/v1",
            ),
        }
        result = build_seed_allowlist(providers)
        assert "my-server:9090" in result

    def test_skips_provider_with_no_base_url(self) -> None:
        providers = {
            "cloud": ProviderConfig(driver="litellm", base_url=None),
        }
        result = build_seed_allowlist(providers)
        # Should just be preset seeds
        assert set(result) == set(seed_from_presets())

    def test_deduplicates_with_presets(self) -> None:
        providers = {
            "ollama": ProviderConfig(
                driver="litellm",
                base_url="http://localhost:11434",
            ),
        }
        result = build_seed_allowlist(providers)
        # localhost:11434 is already in preset seeds
        assert result.count("localhost:11434") == 1

    def test_no_duplicates(self) -> None:
        providers = {
            "a": ProviderConfig(
                driver="litellm",
                base_url="http://my-server:9090",
            ),
            "b": ProviderConfig(
                driver="litellm",
                base_url="http://my-server:9090",
            ),
        }
        result = build_seed_allowlist(providers)
        assert result.count("my-server:9090") == 1

    def test_returns_tuple(self) -> None:
        result = build_seed_allowlist({})
        assert isinstance(result, tuple)


# ── is_url_allowed ───────────────────────────────────────────────


class TestIsUrlAllowed:
    """URL allowlist checking."""

    def test_allowed_url(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("localhost:11434",),
        )
        assert is_url_allowed("http://localhost:11434/api/tags", policy) is True

    def test_disallowed_url(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("localhost:11434",),
        )
        assert is_url_allowed("http://localhost:9999/v1", policy) is False

    def test_case_insensitive(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("host.example.com:8080",),
        )
        assert is_url_allowed("http://HOST.EXAMPLE.COM:8080/v1", policy) is True

    def test_different_port_not_allowed(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("localhost:11434",),
        )
        assert is_url_allowed("http://localhost:1234/v1", policy) is False

    def test_block_private_ips_false_allows_all(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=(),
            block_private_ips=False,
        )
        assert is_url_allowed("http://anything:9999/v1", policy) is True

    def test_empty_allowlist_blocks(self) -> None:
        policy = ProviderDiscoveryPolicy(host_port_allowlist=())
        assert is_url_allowed("http://localhost:11434", policy) is False

    def test_invalid_url_returns_false(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("localhost:11434",),
        )
        assert is_url_allowed("not-a-url", policy) is False

    def test_with_path_still_matches(self) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=("localhost:1234",),
        )
        assert is_url_allowed("http://localhost:1234/v1/models", policy) is True


# ── Property-based tests ─────────────────────────────────────────


class TestDiscoveryPolicyProperties:
    """Hypothesis property-based tests."""

    @given(
        entries=st.lists(
            st.from_regex(r"[a-zA-Z][a-zA-Z0-9\.\-]{0,20}:[0-9]{1,5}", fullmatch=True),
            min_size=0,
            max_size=10,
        ),
    )
    @settings()
    def test_normalization_is_idempotent(self, entries: list[str]) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=tuple(entries),
        )
        policy2 = ProviderDiscoveryPolicy(
            host_port_allowlist=policy.host_port_allowlist,
        )
        assert policy.host_port_allowlist == policy2.host_port_allowlist

    @given(
        entry=st.from_regex(
            r"[a-zA-Z][a-zA-Z0-9\.\-]{0,20}:[0-9]{1,5}",
            fullmatch=True,
        ),
    )
    @settings()
    def test_allowlist_entries_always_lowercase(self, entry: str) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=(entry,),
        )
        for e in policy.host_port_allowlist:
            assert e == e.lower()

    @given(
        entries=st.lists(
            st.from_regex(r"[a-z][a-z0-9\.\-]{0,20}:[0-9]{1,5}", fullmatch=True),
            min_size=0,
            max_size=10,
        ),
    )
    @settings()
    def test_no_duplicates_after_normalization(self, entries: list[str]) -> None:
        policy = ProviderDiscoveryPolicy(
            host_port_allowlist=tuple(entries),
        )
        assert len(policy.host_port_allowlist) == len(set(policy.host_port_allowlist))


# ── resolve_discovery_target (async DNS + pinning) ───────────────


class TestResolveDiscoveryTarget:
    """Async DNS pre-flight layered on the host:port trust gate."""

    @pytest.mark.unit
    async def test_resolves_and_pins_allowlisted_local_host(self) -> None:
        # localhost is intentionally private; an allowlisted discovery
        # host bypasses the private-IP block but is still resolved so the
        # caller can pin the connection.
        policy = ProviderDiscoveryPolicy(host_port_allowlist=("localhost:11434",))
        mock_results = [(0, 0, 0, "", ("127.0.0.1", 0))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.return_value = mock_results
            result = await resolve_discovery_target(
                "http://localhost:11434/api/tags", policy
            )
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == ("127.0.0.1",)

    @pytest.mark.unit
    async def test_dns_failure_returns_error(self) -> None:
        # A trusted host whose DNS cannot resolve yields an error string
        # so the caller skips it rather than probing unpinned.
        policy = ProviderDiscoveryPolicy(host_port_allowlist=("myhost:8080",))
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new_callable=AsyncMock) as mock:
            mock.side_effect = OSError("no such host")
            result = await resolve_discovery_target("http://myhost:8080/v1", policy)
        assert isinstance(result, str)

    @pytest.mark.unit
    async def test_literal_ip_needs_no_pin(self) -> None:
        # A literal-IP target has nothing to pin (no DNS step).
        policy = ProviderDiscoveryPolicy(host_port_allowlist=("172.17.0.1:1234",))
        result = await resolve_discovery_target("http://172.17.0.1:1234/v1", policy)
        assert isinstance(result, DnsValidationOk)
        assert result.resolved_ips == ()

    @pytest.mark.unit
    async def test_unparseable_url_rejected(self) -> None:
        policy = ProviderDiscoveryPolicy()
        result = await resolve_discovery_target("not-a-url", policy)
        assert isinstance(result, str)
