"""Unit tests for ``llms.txt`` documentation-index discovery.

Two properties matter. The probe must never turn a successful fetch into a
failure, since it runs afterwards for convenience only. And it must not report
an index on the many sites that answer any unknown path with a 200 and their
HTML shell, because a fabricated index URL costs the agent a wasted fetch.
"""

import httpx
import pytest
import respx

from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.llms_txt import (
    INDEX_PROBE_TTL_SECONDS,
    IndexProbeCache,
    discover_llms_txt,
    discovery_notice,
    index_urls_for,
    origin_of,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_OPEN_POLICY = NetworkPolicy(block_private_ips=False)
_PAGE = "https://docs.example-provider.test/guide/install"
_INDEX = "https://docs.example-provider.test/llms.txt"
_TIMEOUT = 5.0


class TestIndexUrls:
    def test_derives_both_urls_from_the_origin(self) -> None:
        index, full = index_urls_for(_PAGE)
        assert index == _INDEX
        assert full == "https://docs.example-provider.test/llms-full.txt"

    def test_query_and_fragment_are_dropped(self) -> None:
        index, _ = index_urls_for(f"{_PAGE}?v=2#anchor")
        assert index == _INDEX

    def test_a_url_with_no_host_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot derive an origin"):
            index_urls_for("not-a-url")

    def test_userinfo_is_absent_from_the_derived_urls(self) -> None:
        """These URLs are requested AND shown to the agent.

        Copying the authority wholesale mints a derived string carrying a
        credential the operator only ever put in one place, and puts it
        somewhere they never fetched it from.
        """
        index, full = index_urls_for(
            "https://reader:s3cr3t-token@docs.example-provider.test/guide/install"
        )

        assert index == _INDEX
        assert full == "https://docs.example-provider.test/llms-full.txt"

    def test_a_non_default_port_survives_the_credential_strip(self) -> None:
        """The port routes the request; the userinfo does not."""
        index, _ = index_urls_for("https://u:p@docs.example-provider.test:8443/guide")

        assert index == "https://docs.example-provider.test:8443/llms.txt"

    def test_an_ipv6_authority_keeps_its_brackets(self) -> None:
        index, _ = index_urls_for("http://[2001:db8::1]:8080/guide")

        assert index == "http://[2001:db8::1]:8080/llms.txt"

    def test_an_explicit_zero_port_is_kept_rather_than_normalised_away(self) -> None:
        """Port 0 is stated, and this runs before the network validator.

        Dropping it would rewrite a URL the validator refuses (it rejects any
        port at or below zero) into one it accepts at the scheme default, so
        the probe would go to a different endpoint than the caller named
        instead of failing closed.
        """
        index, _ = index_urls_for("http://docs.example-provider.test:0/guide")

        assert index == "http://docs.example-provider.test:0/llms.txt"

    @pytest.mark.parametrize(
        "url",
        [
            "http://docs.example-provider.test:65536/guide",
            "http://docs.example-provider.test:abc/guide",
            "http://docs.example-provider.test:-1/guide",
        ],
    )
    def test_a_port_that_is_not_a_port_is_refused_rather_than_dropped(
        self,
        url: str,
    ) -> None:
        """This is the consumer the conflation actually reaches.

        The host parses; only reading the port raises. Swallowing that read
        makes ``:65536`` indistinguishable from a portless URL, so the derived
        index URL lands on port 80: an endpoint nobody named, which unlike the
        stated one is reachable, and which this probe would then fetch. The
        network validator refuses the original URL, but it never sees it here,
        because the derived URL is what gets validated.
        """
        with pytest.raises(ValueError, match="cannot derive an origin"):
            index_urls_for(url)

    def test_userinfo_without_a_host_is_rejected(self) -> None:
        """``netloc`` is non-empty here while there is no host to probe."""
        with pytest.raises(ValueError, match="cannot derive an origin"):
            index_urls_for("https://user:pw@/guide")


class TestOriginKey:
    """The index belongs to the origin, not to whoever asked for it."""

    def test_a_credentialed_and_a_bare_read_share_one_entry(self) -> None:
        """Keyed on the raw authority they would occupy separate entries.

        Neither could then answer for the other, and the credential would sit
        in a cache key for the lifetime of the entry.
        """
        credentialed = origin_of("https://u:p@docs.example-provider.test/a")
        bare = origin_of("https://docs.example-provider.test/b")

        assert credentialed == bare
        assert credentialed == ("https", "docs.example-provider.test")

    def test_a_url_with_no_host_has_no_origin(self) -> None:
        assert origin_of("https://user:pw@/guide") is None

    def test_a_malformed_port_has_no_origin_either(self) -> None:
        # Keying it as the bare host would let a stated-but-invalid port share
        # a cache entry with the scheme default.
        assert origin_of("https://docs.example-provider.test:65536/a") is None


class TestDiscovery:
    @respx.mock
    async def test_a_published_index_is_reported(self) -> None:
        respx.get(_INDEX).mock(
            return_value=httpx.Response(
                200,
                text="# Example docs\n\n- [Install](/guide/install)",
                headers={"Content-Type": "text/plain"},
            )
        )
        found = await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT
        )
        assert found == _INDEX

    @respx.mock
    async def test_a_404_reports_nothing(self) -> None:
        respx.get(_INDEX).mock(return_value=httpx.Response(404))
        found = await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT
        )
        assert found == ""

    @respx.mock
    async def test_a_soft_404_html_shell_is_not_an_index(self) -> None:
        """A 200 plus the site's HTML shell is the common unknown-path answer."""
        respx.get(_INDEX).mock(
            return_value=httpx.Response(
                200,
                text="<!DOCTYPE html><html><body>Not found</body></html>",
                headers={"Content-Type": "text/html"},
            )
        )
        found = await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT
        )
        assert found == ""

    @respx.mock
    async def test_html_without_a_content_type_is_still_rejected(self) -> None:
        respx.get(_INDEX).mock(
            return_value=httpx.Response(200, text="<html><body>hi</body></html>")
        )
        found = await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT
        )
        assert found == ""

    @respx.mock
    async def test_an_empty_body_is_not_an_index(self) -> None:
        respx.get(_INDEX).mock(
            return_value=httpx.Response(
                200, text="   ", headers={"Content-Type": "text/plain"}
            )
        )
        found = await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT
        )
        assert found == ""

    @respx.mock
    async def test_a_transport_failure_is_swallowed(self) -> None:
        """The caller's fetch already succeeded; this must not undo that."""
        respx.get(_INDEX).mock(side_effect=httpx.ConnectError("down"))
        found = await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT
        )
        assert found == ""

    async def test_a_blocked_origin_is_not_probed(self) -> None:
        found = await discover_llms_txt(
            "http://169.254.169.254/x",
            network_policy=NetworkPolicy(block_private_ips=True),
            timeout_seconds=_TIMEOUT,
        )
        assert found == ""

    async def test_an_unparseable_url_reports_nothing(self) -> None:
        found = await discover_llms_txt(
            "not-a-url", network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT
        )
        assert found == ""


def _cache(clock: FakeClock) -> IndexProbeCache:
    return IndexProbeCache(clock=clock, ttl_seconds=INDEX_PROBE_TTL_SECONDS)


class TestOriginMemory:
    """The answer belongs to the origin, so asking once is asking enough.

    Reading a library's documentation page by page probes the same host after
    every page. Nineteen of twenty of those requests re-establish what the
    first one already did, against a third-party site we do not own.
    """

    @respx.mock
    async def test_a_second_page_on_the_same_origin_does_not_reprobe(self) -> None:
        route = respx.get(_INDEX).mock(
            return_value=httpx.Response(
                200,
                text="# Example docs\n\n- [Install](/guide/install)",
                headers={"Content-Type": "text/plain"},
            )
        )
        cache = _cache(FakeClock())

        first = await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT, cache=cache
        )
        second = await discover_llms_txt(
            "https://docs.example-provider.test/guide/other",
            network_policy=_OPEN_POLICY,
            timeout_seconds=_TIMEOUT,
            cache=cache,
        )

        assert first == second == _INDEX
        assert route.call_count == 1

    @respx.mock
    async def test_absence_is_remembered_too(self) -> None:
        # The case that matters most: most sites publish nothing, and without
        # caching the miss they are asked again after every single page.
        route = respx.get(_INDEX).mock(return_value=httpx.Response(404))
        cache = _cache(FakeClock())

        for _ in range(3):
            found = await discover_llms_txt(
                _PAGE,
                network_policy=_OPEN_POLICY,
                timeout_seconds=_TIMEOUT,
                cache=cache,
            )
            assert found == ""

        assert route.call_count == 1

    @respx.mock
    async def test_a_different_origin_is_probed_on_its_own(self) -> None:
        respx.get(_INDEX).mock(return_value=httpx.Response(404))
        other = respx.get("https://other.example-provider.test/llms.txt").mock(
            return_value=httpx.Response(
                200,
                text="# Other docs\n\n- [Start](/start)",
                headers={"Content-Type": "text/plain"},
            )
        )
        cache = _cache(FakeClock())

        await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT, cache=cache
        )
        found = await discover_llms_txt(
            "https://other.example-provider.test/guide",
            network_policy=_OPEN_POLICY,
            timeout_seconds=_TIMEOUT,
            cache=cache,
        )

        assert found == "https://other.example-provider.test/llms.txt"
        assert other.call_count == 1

    @respx.mock
    async def test_the_memory_expires_so_a_new_index_is_found(self) -> None:
        # A site that starts publishing an index must be picked up without a
        # restart, which is what bounds how long absence is trusted.
        route = respx.get(_INDEX).mock(return_value=httpx.Response(404))
        clock = FakeClock()
        cache = _cache(clock)
        await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT, cache=cache
        )

        clock.advance(INDEX_PROBE_TTL_SECONDS + 1)
        route.mock(
            return_value=httpx.Response(
                200,
                text="# Example docs\n\n- [Install](/guide/install)",
                headers={"Content-Type": "text/plain"},
            )
        )
        found = await discover_llms_txt(
            _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT, cache=cache
        )

        assert found == _INDEX
        assert route.call_count == 2

    @respx.mock
    async def test_without_a_cache_every_call_probes(self) -> None:
        """The default stays a plain probe; caching is the caller's choice."""
        route = respx.get(_INDEX).mock(return_value=httpx.Response(404))

        for _ in range(2):
            await discover_llms_txt(
                _PAGE, network_policy=_OPEN_POLICY, timeout_seconds=_TIMEOUT
            )

        assert route.call_count == 2

    def test_the_oldest_origin_is_evicted_once_full(self) -> None:
        clock = FakeClock()
        cache = IndexProbeCache(clock=clock, ttl_seconds=INDEX_PROBE_TTL_SECONDS)
        origins = [("https", f"host{index}.test") for index in range(300)]

        for origin in origins:
            cache.put(origin, "")

        assert cache.get(origins[0]) is None
        assert cache.get(origins[-1]) == ""


class TestNotice:
    def test_nothing_discovered_renders_nothing(self) -> None:
        assert discovery_notice("") == ""

    def test_a_discovery_names_the_url(self) -> None:
        notice = discovery_notice(_INDEX)
        assert _INDEX in notice
        assert "index" in notice.lower()
