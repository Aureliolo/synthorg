"""Unit tests for the generic HTTP web-search provider.

The adapter is vendor-agnostic, so these tests drive it with synthetic
``SearchProviderPreset`` fixtures (``example-provider`` names, ``.test``
endpoints) rather than the real production presets: the point under test is the
adapter's request/response handling for the GET and POST contract shapes, not
any one vendor's registry entry (that registry is validated separately in
``test_search_presets.py``). HTTP is mocked with respx; the network policy
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
from synthorg.tools.web.providers.presets import SearchProviderPreset
from synthorg.tools.web.web_search import SearchResult, WebSearchProvider
from tests._shared.fake_clock import FakeClock

_OPEN_POLICY = NetworkPolicy(block_private_ips=False)

# A GET-shaped provider: query + count in the query string, key in a custom
# header, results nested under ``web.results`` with a ``description`` snippet.
_GET_PRESET = SearchProviderPreset(
    id="example-get",
    endpoint="https://search.example-provider.test/get",
    method="GET",
    auth_header="X-Example-Token",
    query_key="q",
    count_key="count",
    max_results_cap=20,
    results_path=("web", "results"),
    snippet_key="description",
)

# A POST-shaped provider: query + count in a JSON body with constant extras, a
# bearer auth header, results at the root ``results`` with a ``content`` snippet.
_POST_PRESET = SearchProviderPreset(
    id="example-post",
    endpoint="https://search.example-provider.test/post",
    method="POST",
    auth_header="Authorization",
    auth_template="Bearer {key}",
    query_key="query",
    count_key="max_results",
    extra={"type": "auto"},
    max_results_cap=20,
    results_path=("results",),
    snippet_key="content",
)


class _StubCatalog:
    """Minimal ConnectionCredentialSource returning fixed credentials."""

    def __init__(self, creds: dict[str, str]) -> None:
        self._creds = creds

    async def get_credentials(self, name: str) -> dict[str, str]:
        del name
        return dict(self._creds)


def _provider(
    preset: SearchProviderPreset,
    *,
    creds: dict[str, str] | None = None,
    max_attempts: int = 3,
) -> HttpWebSearchProvider:
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
        assert isinstance(_provider(_GET_PRESET), WebSearchProvider)


class TestGetShape:
    @pytest.mark.unit
    @respx.mock
    async def test_get_request_shape_and_parsing(self) -> None:
        route = respx.get(url__startswith=_GET_PRESET.endpoint).mock(
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
        results = await _provider(_GET_PRESET).search("python asyncio", max_results=5)

        assert results == [
            SearchResult(
                title="Result A", url="https://a.example", snippet="snippet a"
            ),
            SearchResult(
                title="Result B", url="https://b.example", snippet="snippet b"
            ),
        ]
        request = route.calls.last.request
        assert request.headers["X-Example-Token"] == "secret-key"
        assert request.url.params["q"] == "python asyncio"
        assert request.url.params["count"] == "5"

    @pytest.mark.unit
    @respx.mock
    async def test_count_clamped_to_preset_cap(self) -> None:
        route = respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(200, json={"web": {"results": []}})
        )
        await _provider(_GET_PRESET).search("q", max_results=100)
        # The preset cap is 20; the request must not exceed it.
        assert route.calls.last.request.url.params["count"] == "20"


class TestPostShape:
    @pytest.mark.unit
    @respx.mock
    async def test_post_shape_and_parsing(self) -> None:
        route = respx.post(_POST_PRESET.endpoint).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "T",
                            "url": "https://t.example",
                            "content": "post snippet",
                        }
                    ]
                },
            )
        )
        results = await _provider(_POST_PRESET).search("q", max_results=3)

        assert results == [
            SearchResult(title="T", url="https://t.example", snippet="post snippet")
        ]
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer secret-key"

    @pytest.mark.unit
    @respx.mock
    async def test_post_body_carries_query_count_and_extras(self) -> None:
        import json

        route = respx.post(_POST_PRESET.endpoint).mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        await _provider(_POST_PRESET).search("hello", max_results=4)
        body = json.loads(route.calls.last.request.content)
        assert body["query"] == "hello"
        assert body["max_results"] == 4
        assert body["type"] == "auto"


class TestFailureModes:
    @pytest.mark.unit
    async def test_missing_credential_raises_configuration_error(self) -> None:
        provider = _provider(_GET_PRESET, creds={"base_url": "https://x"})
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
        respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(401, json={})
        )
        with pytest.raises(WebSearchResponseError):
            await _provider(_GET_PRESET).search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_malformed_json_raises_response_error(self) -> None:
        respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(200, text="not json")
        )
        with pytest.raises(WebSearchResponseError):
            await _provider(_GET_PRESET).search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_retryable_status_exhausts_to_transient_error(self) -> None:
        respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(503, json={})
        )
        with pytest.raises(WebSearchTransientError):
            await _provider(_GET_PRESET, max_attempts=2).search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_retryable_status_then_success(self) -> None:
        respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            side_effect=[
                httpx.Response(503, json={}),
                httpx.Response(200, json={"web": {"results": []}}),
            ]
        )
        results = await _provider(_GET_PRESET, max_attempts=3).search("q")
        assert results == []

    @pytest.mark.unit
    async def test_catalog_error_is_wrapped_and_scrubbed(self) -> None:
        """A raising catalog surfaces as a domain error, not a raw exception."""

        class _RaisingCatalog:
            async def get_credentials(self, name: str) -> dict[str, str]:
                del name
                msg = "secret backend unreachable"
                raise RuntimeError(msg)

        provider = HttpWebSearchProvider(
            preset=_GET_PRESET,
            catalog=_RaisingCatalog(),
            connection_name="c",
            network_policy=_OPEN_POLICY,
        )
        with pytest.raises(WebSearchConfigurationError):
            await provider.search("q")

    @pytest.mark.unit
    @respx.mock
    async def test_retry_after_header_captured_on_429(self) -> None:
        """A 429 Retry-After is parsed onto the transient error for backoff."""
        respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "30"}, json={})
        )
        with pytest.raises(WebSearchTransientError) as excinfo:
            await _provider(_GET_PRESET, max_attempts=1).search("q")
        assert excinfo.value.retry_after_seconds == 30.0

    @pytest.mark.unit
    @respx.mock
    async def test_retry_after_header_captured_on_503(self) -> None:
        """Retry-After is honoured for every retryable status, not just 429."""
        respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(503, headers={"Retry-After": "12"}, json={})
        )
        with pytest.raises(WebSearchTransientError) as excinfo:
            await _provider(_GET_PRESET, max_attempts=1).search("q")
        assert excinfo.value.retry_after_seconds == 12.0


class TestMalformedRows:
    @pytest.mark.unit
    @respx.mock
    async def test_whitespace_only_fields_skipped_not_aborting(self) -> None:
        """A whitespace-only title/url row is dropped, not aborting the search.

        ``SearchResult.title``/``url`` are ``NotBlankStr``; without the strip
        guard a "   " row would raise a validation error outside the per-item
        filter and blank the whole page.
        """
        rows = [
            {"title": "   ", "url": "https://blank.example", "description": "x"},
            {"title": "Good", "url": "   ", "description": "y"},
            {"title": "Real", "url": "https://real.example", "description": "z"},
        ]
        respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(200, json={"web": {"results": rows}})
        )
        results = await _provider(_GET_PRESET).search("q", max_results=5)
        assert results == [
            SearchResult(title="Real", url="https://real.example", snippet="z")
        ]


class TestResultCeiling:
    @pytest.mark.unit
    @respx.mock
    async def test_ceiling_below_preset_cap_wins(self) -> None:
        route = respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(200, json={"web": {"results": []}})
        )
        provider = HttpWebSearchProvider(
            preset=_GET_PRESET,
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
        route = respx.get(url__startswith=_GET_PRESET.endpoint).mock(
            return_value=httpx.Response(200, json={"web": {"results": []}})
        )
        provider = HttpWebSearchProvider(
            preset=_GET_PRESET,
            catalog=_StubCatalog({"api_key": "k"}),
            connection_name="c",
            network_policy=_OPEN_POLICY,
            max_results_ceiling=50,  # above the preset cap of 20
        )
        await provider.search("q", max_results=100)
        assert route.calls.last.request.url.params["count"] == "20"


class TestConstructorValidation:
    @pytest.mark.unit
    @pytest.mark.parametrize("timeout", [0.0, -1.0])
    def test_non_positive_timeout_rejected(self, timeout: float) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            HttpWebSearchProvider(
                preset=_GET_PRESET,
                catalog=_StubCatalog({"api_key": "k"}),
                connection_name="c",
                timeout_seconds=timeout,
            )

    @pytest.mark.unit
    def test_non_positive_ceiling_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_results_ceiling"):
            HttpWebSearchProvider(
                preset=_GET_PRESET,
                catalog=_StubCatalog({"api_key": "k"}),
                connection_name="c",
                max_results_ceiling=0,
            )

    @pytest.mark.unit
    def test_blank_connection_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="connection_name"):
            HttpWebSearchProvider(
                preset=_GET_PRESET,
                catalog=_StubCatalog({"api_key": "k"}),
                connection_name="   ",
            )
