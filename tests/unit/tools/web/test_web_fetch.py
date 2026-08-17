"""Unit tests for the ``web_fetch`` tool and its escalation ladder.

The load-bearing behaviours here are ownership ones: the operator decides which
rungs exist, the agent decides which available rung serves a call, and nothing
escalates by itself. A rung that comes back empty must name itself and list
what is left, so the next attempt is a call the agent chose.
"""

import pytest
from pydantic import ValidationError

from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.web_fetch import (
    FetchBackend,
    FetchedPage,
    WebFetchProvider,
    WebFetchTool,
)

pytestmark = pytest.mark.unit

_OPEN_POLICY = NetworkPolicy(block_private_ips=False)
_URL = "https://docs.example-provider.test/api"


class _StubRung:
    """A rung returning a fixed page, or raising."""

    def __init__(
        self,
        backend: FetchBackend,
        *,
        markdown: str = "# Title\n\nBody text.",
        error: Exception | None = None,
        capabilities: tuple[str, ...] = (),
    ) -> None:
        self._backend = backend
        self._markdown = markdown
        self._error = error
        self._capabilities = capabilities
        self.calls: list[str] = []

    @property
    def backend(self) -> FetchBackend:
        return self._backend

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self._capabilities

    async def fetch(self, url: str) -> FetchedPage:
        self.calls.append(url)
        if self._error is not None:
            raise self._error
        return FetchedPage(
            url=url,
            final_url=url,
            title="Title",
            markdown=self._markdown,
            backend=self._backend,
        )


def _tool(
    *rungs: _StubRung,
    discover: bool = False,
) -> WebFetchTool:
    return WebFetchTool(
        providers={r.backend: r for r in rungs},
        network_policy=_OPEN_POLICY,
        discover_docs_index=discover,
    )


class TestConstruction:
    def test_a_rung_satisfies_the_protocol(self) -> None:
        assert isinstance(_StubRung(FetchBackend.LOCAL), WebFetchProvider)

    def test_no_rungs_is_refused_rather_than_registered_useless(self) -> None:
        """A tool that can answer nothing is not registered at all."""
        with pytest.raises(ValueError, match="at least one configured backend"):
            WebFetchTool(providers={})

    def test_description_names_the_configured_backends(self) -> None:
        tool = _tool(_StubRung(FetchBackend.LOCAL), _StubRung(FetchBackend.RENDER))
        assert "local" in tool.description
        assert "render" in tool.description


class TestBackendSelection:
    async def test_defaults_to_the_cheapest_configured_rung(self) -> None:
        local = _StubRung(FetchBackend.LOCAL)
        proxy = _StubRung(FetchBackend.PROXY)
        result = await _tool(local, proxy).execute(arguments={"url": _URL})
        assert result.is_error is False
        assert local.calls == [_URL]
        assert proxy.calls == []

    async def test_defaults_to_the_next_cheapest_when_local_is_absent(self) -> None:
        proxy = _StubRung(FetchBackend.PROXY)
        render = _StubRung(FetchBackend.RENDER)
        await _tool(proxy, render).execute(arguments={"url": _URL})
        assert proxy.calls == [_URL]
        assert render.calls == []

    async def test_the_agent_can_name_a_rung(self) -> None:
        local = _StubRung(FetchBackend.LOCAL)
        render = _StubRung(FetchBackend.RENDER)
        await _tool(local, render).execute(arguments={"url": _URL, "via": "render"})
        assert render.calls == [_URL]
        assert local.calls == []

    async def test_an_unconfigured_rung_is_refused_and_names_what_exists(
        self,
    ) -> None:
        result = await _tool(_StubRung(FetchBackend.LOCAL)).execute(
            arguments={"url": _URL, "via": "render"}
        )
        assert result.is_error is True
        assert "not configured" in result.content
        assert "local" in result.content

    async def test_a_rung_that_does_not_exist_is_rejected_at_the_boundary(
        self,
    ) -> None:
        """An unknown name never reaches the backend lookup.

        "Not configured" is the answer for a real rung the operator did not
        set up; a value that names no rung at all is a malformed argument, and
        the two must not read alike.
        """
        with pytest.raises(ValidationError):
            await _tool(_StubRung(FetchBackend.LOCAL)).execute(
                arguments={"url": _URL, "via": "telepathy"}
            )

    @pytest.mark.parametrize(
        "url",
        [
            "docs.example-provider.test/api",
            "/api/reference",
            "file:///etc/passwd",
            "https://",
        ],
    )
    async def test_a_url_that_is_not_an_absolute_http_url_is_rejected(
        self,
        url: str,
    ) -> None:
        # Shape, not policy: these cannot succeed on any rung, so failing here
        # tells the agent what it got wrong instead of reporting a transport
        # error from a request that was never sendable.
        with pytest.raises(ValidationError):
            await _tool(_StubRung(FetchBackend.LOCAL)).execute(arguments={"url": url})


class TestNoSilentEscalation:
    async def test_an_empty_read_names_the_backend_and_the_rungs_left(self) -> None:
        local = _StubRung(FetchBackend.LOCAL, markdown="")
        render = _StubRung(FetchBackend.RENDER)
        result = await _tool(local, render).execute(arguments={"url": _URL})
        assert "local" in result.content
        assert "render" in result.content
        assert "via" in result.content

    async def test_an_empty_read_does_not_call_another_rung(self) -> None:
        """Escalation is the agent's call, so the transcript stays truthful."""
        local = _StubRung(FetchBackend.LOCAL, markdown="")
        render = _StubRung(FetchBackend.RENDER)
        await _tool(local, render).execute(arguments={"url": _URL})
        assert render.calls == []

    async def test_an_empty_read_is_not_an_error(self) -> None:
        """A page with no prose is an answer, not a failure."""
        result = await _tool(_StubRung(FetchBackend.LOCAL, markdown="")).execute(
            arguments={"url": _URL}
        )
        assert result.is_error is False
        assert result.metadata["result_characters"] == 0

    async def test_the_only_rung_says_there_is_nothing_left(self) -> None:
        result = await _tool(_StubRung(FetchBackend.LOCAL, markdown="")).execute(
            arguments={"url": _URL}
        )
        assert "No other backend is configured." in result.content

    async def test_a_failed_rung_reports_the_rungs_left(self) -> None:
        local = _StubRung(FetchBackend.LOCAL, error=RuntimeError("boom"))
        render = _StubRung(FetchBackend.RENDER)
        result = await _tool(local, render).execute(arguments={"url": _URL})
        assert result.is_error is True
        assert "render" in result.content
        assert render.calls == []

    async def test_a_failure_does_not_leak_the_provider_message(self) -> None:
        local = _StubRung(FetchBackend.LOCAL, error=RuntimeError("key=sk-secret"))
        result = await _tool(local).execute(arguments={"url": _URL})
        assert "sk-secret" not in result.content


class TestEgressPolicy:
    @pytest.mark.parametrize(
        "backend",
        [FetchBackend.LOCAL, FetchBackend.PROXY, FetchBackend.RENDER],
    )
    async def test_a_blocked_target_never_reaches_any_rung(
        self,
        backend: FetchBackend,
    ) -> None:
        """Under proxy the vendor does the fetching, so the ASK must be bound.

        A target the policy refuses would otherwise be fetched by the vendor
        and its cloud-metadata response handed straight back.
        """
        rung = _StubRung(backend)
        tool = WebFetchTool(
            providers={backend: rung},
            network_policy=NetworkPolicy(block_private_ips=True),
        )
        result = await tool.execute(
            arguments={"url": "http://169.254.169.254/latest/meta-data/"}
        )
        assert result.is_error is True
        assert "blocked" in result.content.lower()
        assert rung.calls == []


class TestResultShape:
    async def test_content_carries_the_title_and_source(self) -> None:
        result = await _tool(_StubRung(FetchBackend.LOCAL)).execute(
            arguments={"url": _URL}
        )
        assert "# Title" in result.content
        assert f"Source: {_URL}" in result.content

    async def test_metadata_records_which_backend_answered(self) -> None:
        result = await _tool(_StubRung(FetchBackend.PROXY)).execute(
            arguments={"url": _URL, "via": "proxy"}
        )
        assert result.metadata["backend"] == "proxy"
