"""Unit tests for the local and proxy fetch rungs.

Both are driven with synthetic presets against mocked HTTP, so what is under
test is the adapter's request and response handling rather than any one
vendor's registry entry (that registry is covered in ``test_fetch_presets``).
"""

import httpx
import pytest
import respx

from synthorg.core.resilience.general_retry import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.http_vendor import HttpVendorPreset
from synthorg.observability.events.web import WEB_FETCH_RETRY
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.errors import (
    WebFetchConfigurationError,
    WebFetchEgressBlockedError,
    WebFetchResponseError,
    WebFetchTransientError,
)
from synthorg.tools.web.providers.fetch_presets import FetchProviderPreset
from synthorg.tools.web.providers.http_fetch_provider import HttpWebFetchProvider
from synthorg.tools.web.providers.local_fetch_provider import LocalFetchProvider
from synthorg.tools.web.web_fetch import FetchBackend, WebFetchProvider
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit

_OPEN_POLICY = NetworkPolicy(block_private_ips=False)
_TARGET = "https://docs.example-provider.test/api"
_READER = "https://api.example-provider.test/read"
_BUDGET = 50_000

_DOCS_HTML = (
    "<html><head><title>Widget API</title></head><body>"
    "<nav><a href='/x'>Navigation link</a></nav>"
    "<main><h1>Widget API</h1>"
    "<p>The <code>Widget</code> class renders a widget.</p>"
    "<pre><code>import acme\nacme.go(retries=3)\n</code></pre></main>"
    "<footer>Copyright notice</footer></body></html>"
)


def _local(**overrides: object) -> LocalFetchProvider:
    kwargs: dict[str, object] = {
        "network_policy": _OPEN_POLICY,
        "max_response_bytes": 1_048_576,
        "char_budget": _BUDGET,
        "timeout_seconds": 10.0,
        "user_agent": "TestBot/1.0",
    }
    kwargs.update(overrides)
    return LocalFetchProvider(**kwargs)  # type: ignore[arg-type]


class TestLocalRung:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(_local(), WebFetchProvider)

    def test_identifies_itself(self) -> None:
        assert _local().backend is FetchBackend.LOCAL

    @respx.mock
    async def test_extracts_a_page_to_markdown(self) -> None:
        respx.get(_TARGET).mock(
            return_value=httpx.Response(200, html=_DOCS_HTML),
        )
        page = await _local().fetch(_TARGET)
        assert "# Widget API" in page.markdown
        assert "acme.go(retries=3)" in page.markdown
        assert page.title == "Widget API"
        assert page.backend is FetchBackend.LOCAL

    @respx.mock
    async def test_drops_the_page_chrome(self) -> None:
        respx.get(_TARGET).mock(
            return_value=httpx.Response(200, html=_DOCS_HTML),
        )
        page = await _local().fetch(_TARGET)
        assert "Navigation link" not in page.markdown
        assert "Copyright notice" not in page.markdown

    @respx.mock
    async def test_sends_the_configured_user_agent(self) -> None:
        route = respx.get(_TARGET).mock(
            return_value=httpx.Response(200, html=_DOCS_HTML),
        )
        await _local(user_agent="SynthOrgBot/9.9").fetch(_TARGET)
        assert route.calls.last.request.headers["User-Agent"] == "SynthOrgBot/9.9"

    @respx.mock
    async def test_a_4xx_is_a_response_error(self) -> None:
        respx.get(_TARGET).mock(return_value=httpx.Response(404))
        with pytest.raises(WebFetchResponseError):
            await _local().fetch(_TARGET)

    @respx.mock
    async def test_a_transport_failure_is_transient(self) -> None:
        respx.get(_TARGET).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(WebFetchTransientError):
            await _local().fetch(_TARGET)

    async def test_a_private_target_is_blocked(self) -> None:
        provider = _local(network_policy=NetworkPolicy(block_private_ips=True))
        with pytest.raises(WebFetchEgressBlockedError):
            await provider.fetch("http://169.254.169.254/latest/meta-data/")

    async def test_a_non_http_scheme_is_blocked(self) -> None:
        with pytest.raises(WebFetchEgressBlockedError):
            await _local().fetch("file:///etc/passwd")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_response_bytes", 0),
            ("char_budget", 0),
            ("timeout_seconds", 0.0),
        ],
    )
    def test_a_non_positive_bound_is_refused(
        self,
        field: str,
        value: object,
    ) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            _local(**{field: value})


def _vendor(reader: str = _READER) -> HttpVendorPreset:
    return HttpVendorPreset(
        id=NotBlankStr("example-provider"),
        label=NotBlankStr("Example"),
        base_url=NotBlankStr("https://api.example-provider.test/search"),
        auth_header=NotBlankStr("Authorization"),
        auth_template=NotBlankStr("Bearer {key}"),
        reader_url=reader,
    )


_FLAT_MARKDOWN = FetchProviderPreset(
    vendor=_vendor(),
    url_key="url",
    content_key="content",
    links_key="links",
)

_BATCH_HTML = FetchProviderPreset(
    vendor=_vendor(),
    url_key="urls",
    url_as_list=True,
    results_path=("results",),
    result_is_list=True,
    content_key="raw",
    content_is_markdown=False,
)


class _StubCatalog:
    def __init__(self, creds: dict[str, str]) -> None:
        self._creds = creds

    async def get_credentials(self, name: str) -> dict[str, str]:
        del name
        return dict(self._creds)


def _proxy(
    preset: FetchProviderPreset,
    *,
    creds: dict[str, str] | None = None,
) -> HttpWebFetchProvider:
    handler = GeneralRetryHandler(
        retryable=lambda exc: isinstance(exc, WebFetchTransientError),
        max_attempts=2,
        base=0.0,
        cap=0.0,
        event=WEB_FETCH_RETRY,
        clock=FakeClock(),
    )
    return HttpWebFetchProvider(
        preset=preset,
        catalog=_StubCatalog(creds if creds is not None else {"api_key": "k"}),
        connection_name="reader-conn",
        char_budget=_BUDGET,
        network_policy=_OPEN_POLICY,
        retry_handler=handler,
    )


class TestProxyRung:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(_proxy(_FLAT_MARKDOWN), WebFetchProvider)

    def test_identifies_itself(self) -> None:
        assert _proxy(_FLAT_MARKDOWN).backend is FetchBackend.PROXY

    @respx.mock
    async def test_reads_a_flat_markdown_response(self) -> None:
        respx.post(_READER).mock(
            return_value=httpx.Response(
                200,
                json={
                    "title": "Widget API",
                    "content": "# Widget API\n\nBody.",
                    "links": ["https://example.test/a"],
                },
            )
        )
        page = await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)
        assert page.markdown == "# Widget API\n\nBody."
        assert page.title == "Widget API"
        assert page.links == ("https://example.test/a",)

    @respx.mock
    async def test_sends_the_target_under_the_declared_key(self) -> None:
        route = respx.post(_READER).mock(
            return_value=httpx.Response(200, json={"content": "x"})
        )
        await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)
        assert route.calls.last.request.content == (
            b'{"url":"https://docs.example-provider.test/api"}'
        )

    @respx.mock
    async def test_a_batch_shaped_reader_gets_a_list(self) -> None:
        route = respx.post(_READER).mock(
            return_value=httpx.Response(200, json={"results": [{"raw": "<p>x</p>"}]})
        )
        await _proxy(_BATCH_HTML).fetch(_TARGET)
        assert b'"urls":["https://docs.example-provider.test/api"]' in (
            route.calls.last.request.content
        )

    @respx.mock
    async def test_html_from_a_reader_goes_through_the_same_extractor(self) -> None:
        """Every rung must yield comparable markdown, or a comparison lies."""
        respx.post(_READER).mock(
            return_value=httpx.Response(200, json={"results": [{"raw": _DOCS_HTML}]})
        )
        page = await _proxy(_BATCH_HTML).fetch(_TARGET)
        assert "# Widget API" in page.markdown
        assert "Navigation link" not in page.markdown

    @respx.mock
    async def test_the_auth_header_is_rendered(self) -> None:
        route = respx.post(_READER).mock(
            return_value=httpx.Response(200, json={"content": "x"})
        )
        await _proxy(_FLAT_MARKDOWN, creds={"api_key": "secret"}).fetch(_TARGET)
        assert route.calls.last.request.headers["Authorization"] == "Bearer secret"

    @respx.mock
    async def test_an_unexpected_shape_reports_empty_rather_than_raising(
        self,
    ) -> None:
        respx.post(_READER).mock(return_value=httpx.Response(200, json={"other": 1}))
        page = await _proxy(_BATCH_HTML).fetch(_TARGET)
        assert page.markdown == ""

    @respx.mock
    async def test_a_retryable_status_is_transient(self) -> None:
        respx.post(_READER).mock(return_value=httpx.Response(503))
        with pytest.raises(WebFetchTransientError):
            await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)

    @respx.mock
    async def test_a_4xx_is_not_retried(self) -> None:
        route = respx.post(_READER).mock(return_value=httpx.Response(400))
        with pytest.raises(WebFetchResponseError):
            await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)
        assert route.call_count == 1

    @respx.mock
    async def test_malformed_json_is_a_response_error(self) -> None:
        respx.post(_READER).mock(return_value=httpx.Response(200, text="not json"))
        with pytest.raises(WebFetchResponseError):
            await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)

    async def test_a_missing_credential_is_a_configuration_error(self) -> None:
        with pytest.raises(WebFetchConfigurationError):
            await _proxy(_FLAT_MARKDOWN, creds={}).fetch(_TARGET)

    async def test_a_non_http_target_is_refused_before_the_vendor_is_called(
        self,
    ) -> None:
        """The vendor would fetch whatever we ask for, so the ask is bound."""
        with pytest.raises(WebFetchEgressBlockedError):
            await _proxy(_FLAT_MARKDOWN).fetch("file:///etc/passwd")
