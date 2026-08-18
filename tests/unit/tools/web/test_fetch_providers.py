"""Unit tests for the local and proxy fetch rungs.

Both are driven with synthetic presets against mocked HTTP, so what is under
test is the adapter's request and response handling rather than any one
vendor's registry entry (that registry is covered in ``test_fetch_presets``).
"""

import json

import httpx
import pytest
import respx
from pydantic import ValidationError

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
from synthorg.tools.web.extract import TRUNCATION_MARKER
from synthorg.tools.web.fetch_types import FetchBackend, FetchBudget, WebFetchProvider
from synthorg.tools.web.providers.fetch_presets import FetchProviderPreset
from synthorg.tools.web.providers.http_fetch_provider import HttpWebFetchProvider
from synthorg.tools.web.providers.local_fetch_provider import LocalFetchProvider
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit

_OPEN_POLICY = NetworkPolicy(block_private_ips=False)
_TARGET = "https://docs.example-provider.test/api"
_READER = "https://api.example-provider.test/read"
_BUDGET = 50_000
_MAX_RESPONSE_BYTES = 5_000_000

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
        "budget": FetchBudget(max_response_bytes=1_048_576, char_budget=_BUDGET),
        "timeout_seconds": 10.0,
        "user_agent": "TestBot/1.0",
    }
    kwargs.update(overrides)
    return LocalFetchProvider(**kwargs)  # type: ignore[arg-type]


class TestLocalRungReadsWhatTheOriginActuallySent:
    """Decoding and redirects, where a wrong assumption looks like a blank page."""

    @respx.mock
    async def test_a_declared_charset_is_honoured(self) -> None:
        # Assuming UTF-8 turns every non-ASCII byte into a replacement
        # character, and the extractor then reports a page with no readable
        # content rather than one read with the wrong alphabet.
        body = (
            "<html><head><title>Café</title></head><body><main>"
            "<h1>Café</h1><p>Un café très chaud, servi à la française.</p>"
            "</main></body></html>"
        ).encode("windows-1252")
        respx.get(_TARGET).mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"Content-Type": "text/html; charset=windows-1252"},
            ),
        )

        page = await _local().fetch(_TARGET)

        assert "Café" in page.markdown
        assert "�" not in page.markdown

    @respx.mock
    async def test_an_unknown_charset_label_falls_back_rather_than_raising(
        self,
    ) -> None:
        # The label is unusable; the bytes are not.
        respx.get(_TARGET).mock(
            return_value=httpx.Response(
                200,
                content=_DOCS_HTML.encode(),
                headers={"Content-Type": "text/html; charset=not-a-real-charset"},
            ),
        )

        page = await _local().fetch(_TARGET)

        assert "# Widget API" in page.markdown

    @respx.mock
    async def test_a_redirect_is_reported_with_its_destination(self) -> None:
        # Redirects are not followed, so extracting the 3xx stub would hand
        # the agent an empty page and call it a success.
        respx.get(_TARGET).mock(
            return_value=httpx.Response(
                301,
                headers={"Location": "https://docs.example-provider.test/v2/api"},
            ),
        )

        with pytest.raises(WebFetchResponseError, match="v2/api"):
            await _local().fetch(_TARGET)

    @respx.mock
    async def test_a_redirect_without_a_location_still_fails(self) -> None:
        respx.get(_TARGET).mock(return_value=httpx.Response(302))

        with pytest.raises(WebFetchResponseError, match="redirected"):
            await _local().fetch(_TARGET)


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

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    @respx.mock
    async def test_an_ask_again_status_is_transient(self, status: int) -> None:
        """A rate limit or an outage says "ask again", not "unreadable".

        Reported as a response error, the caller's retry path never runs and
        the agent escalates to a rung that costs money for a page that would
        have answered on its own.
        """
        respx.get(_TARGET).mock(return_value=httpx.Response(status))

        with pytest.raises(WebFetchTransientError):
            await _local().fetch(_TARGET)

    @respx.mock
    async def test_the_origins_own_cooldown_is_carried(self) -> None:
        respx.get(_TARGET).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "12"}),
        )

        with pytest.raises(WebFetchTransientError) as caught:
            await _local().fetch(_TARGET)

        assert caught.value.retry_after_seconds == 12.0

    @respx.mock
    async def test_a_permanent_5xx_is_not_transient(self) -> None:
        """501 names a condition retrying cannot change."""
        respx.get(_TARGET).mock(return_value=httpx.Response(501))

        with pytest.raises(WebFetchResponseError):
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
        [("timeout_seconds", 0.0)],
    )
    def test_a_non_positive_bound_is_refused(
        self,
        field: str,
        value: object,
    ) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            _local(**{field: value})


@pytest.mark.parametrize(
    "ceilings",
    [
        {"max_response_bytes": 0, "char_budget": _BUDGET},
        {"max_response_bytes": 1024, "char_budget": 0},
        {"max_response_bytes": -1, "char_budget": _BUDGET},
    ],
)
def test_a_budget_refuses_a_non_positive_ceiling(ceilings: dict[str, int]) -> None:
    """Both rungs read their ceilings from here, so this is where they hold.

    A zero ceiling would otherwise mean 'accept nothing' at one rung and be
    validated at the other, depending on which constructor happened to check.
    """
    with pytest.raises(ValidationError):
        FetchBudget(**ceilings)


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
    network_policy: NetworkPolicy | None = None,
    max_response_bytes: int = _MAX_RESPONSE_BYTES,
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
        budget=FetchBudget(
            max_response_bytes=max_response_bytes,
            char_budget=_BUDGET,
        ),
        network_policy=network_policy or _OPEN_POLICY,
        retry_handler=handler,
    )


class TestProxyRungIsBoundedLikeEveryOtherRung:
    """The reader is a third party, so its reply is read under a ceiling."""

    @respx.mock
    async def test_an_oversized_reader_reply_does_not_reach_memory_whole(
        self,
    ) -> None:
        # Buffering the body before judging its size is how a reader that
        # answers with a gigabyte takes the process down. Cut short, the
        # remainder is not valid JSON, which surfaces as a response error
        # rather than as a page built from a truncated document.
        oversized = json.dumps({"content": "x" * 10_000})
        respx.post(_READER).mock(return_value=httpx.Response(200, content=oversized))

        with pytest.raises(WebFetchResponseError, match="malformed JSON"):
            await _proxy(_FLAT_MARKDOWN, max_response_bytes=256).fetch(_TARGET)

    @respx.mock
    async def test_a_reply_inside_the_ceiling_is_read_normally(self) -> None:
        payload = json.dumps({"content": "# Widget API"})
        respx.post(_READER).mock(return_value=httpx.Response(200, content=payload))

        page = await _proxy(
            _FLAT_MARKDOWN,
            max_response_bytes=len(payload.encode()),
        ).fetch(_TARGET)

        assert page.markdown == "# Widget API"

    def test_a_non_positive_ceiling_is_refused_at_construction(self) -> None:
        with pytest.raises(ValidationError, match="max_response_bytes"):
            _proxy(_FLAT_MARKDOWN, max_response_bytes=0)


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

    async def test_a_private_target_host_is_refused_by_the_provider_itself(
        self,
    ) -> None:
        """Defence in depth against a metadata address reaching the vendor.

        This rung never opens a socket to the target, so nothing about the
        connection protects it: the vendor fetches whatever we name and hands
        the answer back. The tool checks the same URL first, and this check
        standing on its own is what keeps the module's promise true for any
        caller that reaches a provider directly.
        """
        provider = _proxy(
            _FLAT_MARKDOWN,
            network_policy=NetworkPolicy(block_private_ips=True),
        )
        with pytest.raises(WebFetchEgressBlockedError):
            await provider.fetch("http://169.254.169.254/latest/meta-data/")

    @respx.mock
    async def test_a_truncated_markdown_reader_says_so_in_the_content(self) -> None:
        """The flag alone is metadata, which no model reads.

        A reader whose content arrives already-markdown skips the extractor, so
        this is the path where a cut page could come back looking complete.
        """
        respx.post(_READER).mock(
            return_value=httpx.Response(
                200,
                json={"content": "para\n\n" + ("z" * (_BUDGET * 2))},
            )
        )
        page = await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)
        assert page.truncated is True
        assert TRUNCATION_MARKER.strip() in page.markdown

    @respx.mock
    async def test_an_untruncated_markdown_reader_carries_no_notice(self) -> None:
        respx.post(_READER).mock(
            return_value=httpx.Response(200, json={"content": "# Short\n\nBody."})
        )
        page = await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)
        assert page.truncated is False
        assert TRUNCATION_MARKER.strip() not in page.markdown

    @respx.mock
    async def test_retry_after_is_carried_off_a_429(self) -> None:
        """Without this the retry ladder ignores the delay the vendor asked for."""
        respx.post(_READER).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "7"})
        )
        with pytest.raises(WebFetchTransientError) as caught:
            await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)
        assert caught.value.retry_after_seconds == pytest.approx(7.0)

    @respx.mock
    async def test_retry_after_is_carried_off_a_503(self) -> None:
        respx.post(_READER).mock(
            return_value=httpx.Response(503, headers={"Retry-After": "3"})
        )
        with pytest.raises(WebFetchTransientError) as caught:
            await _proxy(_FLAT_MARKDOWN).fetch(_TARGET)
        assert caught.value.retry_after_seconds == pytest.approx(3.0)
