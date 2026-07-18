"""Unit tests for the generic HTTP web-search provider.

HTTP is mocked with respx; the provider is driven with a network policy that
disables private-IP blocking so no real DNS/pinning happens (the SSRF path is
covered separately with a literal private endpoint that needs no network).
"""

import httpx
import pytest
import respx

from synthorg.core.resilience.general_retry import GeneralRetryHandler
from synthorg.observability.events.web import WEB_SEARCH_RETRY
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.errors import (
    WebSearchConfigurationError,
    WebSearchEgressBlockedError,
    WebSearchResponseError,
    WebSearchTransientError,
)
from synthorg.tools.web.providers.http_search_provider import HttpWebSearchProvider
from synthorg.tools.web.providers.presets import (
    SearchProviderPreset,
    get_search_preset,
)
from synthorg.tools.web.web_search import SearchResult, WebSearchProvider
from tests._shared.fake_clock import FakeClock

_OPEN_POLICY = NetworkPolicy(block_private_ips=False)


class _StubCatalog:
    """Minimal ConnectionCredentialSource returning fixed credentials."""

    def __init__(self, creds: dict[str, str]) -> None:
        self._creds = creds

    async def get_credentials(self, name: str) -> dict[str, str]:
        del name
        return dict(self._creds)


def _provider(
    provider_id: str,
    *,
    creds: dict[str, str] | None = None,
    max_attempts: int = 3,
) -> HttpWebSearchProvider:
    preset = get_search_preset(provider_id)
    assert preset is not None
    handler = GeneralRetryHandler(
        retryable=lambda exc: isinstance(exc, WebSearchTransientError),
        max_attempts=max_attempts,
        base=0.0,
        cap=0.0,
        event=WEB_SEARCH_RETRY,
        clock=FakeClock(),
    )
    return HttpWebSearchProvider(
        preset=preset,
        catalog=_StubCatalog(creds or {"api_key": "secret-key"}),
        connection_name="search-conn",
        network_policy=_OPEN_POLICY,
        retry_handler=handler,
    )


class TestProviderContract:
    @pytest.mark.unit
    def test_satisfies_protocol(self) -> None:
        assert isinstance(_provider("brave"), WebSearchProvider)


class TestBrave:
    @pytest.mark.unit
    @respx.mock
    async def test_get_request_shape_and_parsing(self) -> None:
        route = respx.get(
            url__startswith="https://api.search.brave.com/res/v1/web/search"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "Result A",
                                "url": "https://a.example",
                                "description": "snippet a",
                            },
                            {
                                "title": "Result B",
                                "url": "https://b.example",
                                "description": "snippet b",
                            },
                        ]
                    }
                },
            )
        )
        results = await _provider("brave").search("python asyncio", max_results=5)

        assert results == [
            SearchResult(
                title="Result A", url="https://a.example", snippet="snippet a"
            ),
            SearchResult(
                title="Result B", url="https://b.example", snippet="snippet b"
            ),
        ]
        request = route.calls.last.request
        assert request.headers["X-Subscription-Token"] == "secret-key"
        assert request.url.params["q"] == "python asyncio"
        assert request.url.params["count"] == "5"

    @pytest.mark.unit
    @respx.mock
    async def test_count_clamped_to_preset_cap(self) -> None:
        route = respx.get(
            url__startswith="https://api.search.brave.com/res/v1/web/search"
        ).mock(return_value=httpx.Response(200, json={"web": {"results": []}}))
        await _provider("brave").search("q", max_results=100)
        # Brave's cap is 20; the request must not exceed it.
        assert route.calls.last.request.url.params["count"] == "20"


class TestTavilyAndExa:
    @pytest.mark.unit
    @respx.mock
    async def test_tavily_post_shape_and_parsing(self) -> None:
        route = respx.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "T",
                            "url": "https://t.example",
                            "content": "tavily snippet",
                        }
                    ]
                },
            )
        )
        results = await _provider("tavily").search("q", max_results=3)

        assert results == [
            SearchResult(title="T", url="https://t.example", snippet="tavily snippet")
        ]
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer secret-key"

    @pytest.mark.unit
    @respx.mock
    async def test_exa_post_shape_and_parsing(self) -> None:
        route = respx.post("https://api.exa.ai/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "E", "url": "https://e.example", "text": "exa text"}
                    ]
                },
            )
        )
        results = await _provider("exa").search("q", max_results=3)

        assert results == [
            SearchResult(title="E", url="https://e.example", snippet="exa text")
        ]
        assert route.calls.last.request.headers["x-api-key"] == "secret-key"


class TestFailureModes:
    @pytest.mark.unit
    async def test_missing_credential_raises_configuration_error(self) -> None:
        provider = _provider("brave", creds={"base_url": "https://x"})
        with pytest.raises(WebSearchConfigurationError):
            await provider.search("q")

    @pytest.mark.unit
    async def test_private_endpoint_blocked(self) -> None:
        preset = SearchProviderPreset(
            id="loopback",
            endpoint="https://127.0.0.1/search",
            method="GET",
            auth_header="X-Key",
            query_key="q",
            max_results_cap=10,
            snippet_key="snippet",
        )
        provider = HttpWebSearchProvider(
            preset=preset,
            catalog=_StubCatalog({"api_key": "k"}),
            connection_name="c",
            network_policy=NetworkPolicy(),  # blocks private IPs
        )
        with pytest.raises(WebSearchEgressBlockedError):
            await provider.search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_non_retryable_status_raises_response_error(self) -> None:
        respx.get(url__startswith="https://api.search.brave.com").mock(
            return_value=httpx.Response(401, json={})
        )
        with pytest.raises(WebSearchResponseError):
            await _provider("brave").search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_malformed_json_raises_response_error(self) -> None:
        respx.get(url__startswith="https://api.search.brave.com").mock(
            return_value=httpx.Response(200, text="not json")
        )
        with pytest.raises(WebSearchResponseError):
            await _provider("brave").search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_retryable_status_exhausts_to_transient_error(self) -> None:
        respx.get(url__startswith="https://api.search.brave.com").mock(
            return_value=httpx.Response(503, json={})
        )
        with pytest.raises(WebSearchTransientError):
            await _provider("brave", max_attempts=2).search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_retryable_status_then_success(self) -> None:
        respx.get(url__startswith="https://api.search.brave.com").mock(
            side_effect=[
                httpx.Response(503, json={}),
                httpx.Response(200, json={"web": {"results": []}}),
            ]
        )
        results = await _provider("brave", max_attempts=3).search("q")
        assert results == []

    @pytest.mark.unit
    async def test_catalog_error_is_wrapped_and_scrubbed(self) -> None:
        """A raising catalog surfaces as a domain error, not a raw exception."""

        class _RaisingCatalog:
            async def get_credentials(self, name: str) -> dict[str, str]:
                del name
                msg = "secret backend unreachable"
                raise RuntimeError(msg)

        preset = get_search_preset("brave")
        assert preset is not None
        provider = HttpWebSearchProvider(
            preset=preset,
            catalog=_RaisingCatalog(),
            connection_name="c",
            network_policy=_OPEN_POLICY,
        )
        with pytest.raises(WebSearchConfigurationError):
            await provider.search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_retry_after_header_captured_on_transient(self) -> None:
        """A 429 Retry-After is parsed onto the transient error for backoff."""
        respx.get(url__startswith="https://api.search.brave.com").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "30"}, json={})
        )
        with pytest.raises(WebSearchTransientError) as excinfo:
            await _provider("brave", max_attempts=1).search("q")
        assert excinfo.value.retry_after_seconds == 30.0


class TestResultCeiling:
    @pytest.mark.unit
    @respx.mock
    async def test_ceiling_below_preset_cap_wins(self) -> None:
        route = respx.get(
            url__startswith="https://api.search.brave.com/res/v1/web/search"
        ).mock(return_value=httpx.Response(200, json={"web": {"results": []}}))
        preset = get_search_preset("brave")
        assert preset is not None
        provider = HttpWebSearchProvider(
            preset=preset,
            catalog=_StubCatalog({"api_key": "k"}),
            connection_name="c",
            network_policy=_OPEN_POLICY,
            max_results_ceiling=3,
        )
        await provider.search("q", max_results=10)
        assert route.calls.last.request.url.params["count"] == "3"

    @pytest.mark.unit
    @respx.mock
    async def test_preset_cap_wins_when_ceiling_higher(self) -> None:
        route = respx.get(
            url__startswith="https://api.search.brave.com/res/v1/web/search"
        ).mock(return_value=httpx.Response(200, json={"web": {"results": []}}))
        preset = get_search_preset("brave")
        assert preset is not None
        provider = HttpWebSearchProvider(
            preset=preset,
            catalog=_StubCatalog({"api_key": "k"}),
            connection_name="c",
            network_policy=_OPEN_POLICY,
            max_results_ceiling=50,  # above Brave's cap of 20
        )
        await provider.search("q", max_results=100)
        assert route.calls.last.request.url.params["count"] == "20"


class TestConstructorValidation:
    @pytest.mark.unit
    @pytest.mark.parametrize("timeout", [0.0, -1.0])
    def test_non_positive_timeout_rejected(self, timeout: float) -> None:
        preset = get_search_preset("brave")
        assert preset is not None
        with pytest.raises(ValueError, match="timeout_seconds"):
            HttpWebSearchProvider(
                preset=preset,
                catalog=_StubCatalog({"api_key": "k"}),
                connection_name="c",
                timeout_seconds=timeout,
            )

    @pytest.mark.unit
    def test_non_positive_ceiling_rejected(self) -> None:
        preset = get_search_preset("brave")
        assert preset is not None
        with pytest.raises(ValueError, match="max_results_ceiling"):
            HttpWebSearchProvider(
                preset=preset,
                catalog=_StubCatalog({"api_key": "k"}),
                connection_name="c",
                max_results_ceiling=0,
            )

    @pytest.mark.unit
    def test_blank_connection_name_rejected(self) -> None:
        preset = get_search_preset("brave")
        assert preset is not None
        with pytest.raises(ValueError, match="connection_name"):
            HttpWebSearchProvider(
                preset=preset,
                catalog=_StubCatalog({"api_key": "k"}),
                connection_name="   ",
            )
