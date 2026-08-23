"""The guarded-GET primitives shared by ``http_request`` and ``web_fetch``.

``pin_url`` rewrites a plain-HTTP URL onto the IP the SSRF check validated,
which closes the DNS-rebinding window between the check and the connection.
Doing that means the connection no longer carries the hostname, so the ``Host``
header becomes the only thing telling the origin which site was asked for.
"""

from collections.abc import Iterable
from typing import override
from unittest.mock import Mock
from urllib.parse import urlparse

import httpcore
import pytest

from synthorg.tools._dns_pinning import SOCKET_OPTION, PinnedDnsBackend
from synthorg.tools.errors import ToolExecutionError, ToolParameterError
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    validate_url_host,
)
from synthorg.tools.web._guarded_fetch import _pinned_transport, pin_url
from synthorg.tools.web.http_request import HttpRequestTool


def _validation(
    hostname: str,
    *,
    ips: tuple[str, ...] = ("93.184.216.34",),
    is_https: bool = False,
    port: int | None = None,
) -> DnsValidationOk:
    return DnsValidationOk(
        hostname=hostname,
        resolved_ips=ips,
        is_https=is_https,
        port=port,
    )


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    """Records what address the pinned backend actually dialled."""

    def __init__(self, dialled: list[tuple[str, int]]) -> None:
        self._dialled = dialled

    @override
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Record the target instead of opening a socket.

        Returns:
            A stand-in stream; nothing here reads from it.
        """
        del timeout, local_address, socket_options
        self._dialled.append((host, port))
        return Mock(spec=httpcore.AsyncNetworkStream)


@pytest.mark.unit
class TestHostHeaderCarriesTheAuthority:
    """``Host`` is the authority, and an authority includes its port."""

    def test_a_non_default_port_travels_with_the_hostname(self) -> None:
        # A target virtual-hosting on a non-default port routes on this
        # header. Sending the bare name asks for whatever answers on the
        # default port instead, which is a different site.
        _, headers = pin_url(
            "http://example.test:8080/docs",
            {},
            _validation("example.test"),
        )

        assert headers["Host"] == "example.test:8080"

    def test_an_explicit_zero_port_is_not_read_as_absent(self) -> None:
        """``:0`` is a stated port, and a truthiness test drops it.

        The header is the milder half: the same expression feeds the derived
        URLs the ``llms.txt`` probe builds BEFORE the network validator runs,
        where dropping the port turns a URL the validator refuses into one it
        accepts at the scheme default.
        """
        _, headers = pin_url(
            "http://example.test:0/docs",
            {},
            _validation("example.test"),
        )

        assert headers["Host"] == "example.test:0"

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.test:65536/docs",
            "http://example.test:abc/docs",
            "http://example.test:-1/docs",
        ],
    )
    def test_a_port_that_is_not_a_port_is_refused_rather_than_dropped(
        self,
        url: str,
    ) -> None:
        """Answering with the bare host would name a different endpoint.

        ``urlsplit`` parses the hostname happily and only raises when the port
        is read, so swallowing that read turns ``:65536`` into "no port
        stated". The request would then go to the scheme default, which is not
        where the caller pointed it, and which answers.
        """
        with pytest.raises(ToolParameterError, match="no usable authority"):
            pin_url(url, {}, _validation("example.test"))

    def test_a_url_with_no_host_is_refused_too(self) -> None:
        # The alternative is a request carrying ``Host: ""``, which names no
        # site at all.
        with pytest.raises(ToolParameterError, match="no usable authority"):
            pin_url("http://user:pw@/docs", {}, _validation("example.test"))

    def test_the_refusal_does_not_echo_the_url(self) -> None:
        """A rejected authority is exactly where credentials sit unparsed.

        ``redact_url`` cannot help: it rebuilds around a parsed hostname and
        returns its input untouched when there is none, which is this branch.
        """
        with pytest.raises(ToolParameterError, match="no usable authority") as excinfo:
            pin_url("http://user:hunter2@/docs", {}, _validation("example.test"))

        assert "hunter2" not in str(excinfo.value)

    def test_an_address_that_is_not_one_is_refused_rather_than_left_unpinned(
        self,
    ) -> None:
        """Falling back to the hostname answers an unpinned request.

        ``is_blocked_ip`` is fail-closed on an unparseable address, so nothing
        reachable puts one in ``resolved_ips``; the invariant is asserted here
        because the alternative behaviour is indistinguishable from success at
        every layer above, which is what makes it worth a raise.
        """
        with pytest.raises(ToolParameterError, match="not one"):
            pin_url(
                "http://example.test/docs",
                {},
                _validation("example.test", ips=("not-an-address",)),
            )

    def test_a_portless_url_sends_a_bare_host(self) -> None:
        _, headers = pin_url(
            "http://example.test/docs",
            {},
            _validation("example.test"),
        )

        assert headers["Host"] == "example.test"

    def test_an_ipv6_literal_keeps_its_brackets(self) -> None:
        # Without them the address's own colons are indistinguishable from
        # the port separator.
        _, headers = pin_url(
            "http://[2001:db8::1]:8080/docs",
            {},
            _validation("2001:db8::1", ips=()),
        )

        assert headers["Host"] == "[2001:db8::1]:8080"

    def test_userinfo_is_not_part_of_the_host(self) -> None:
        # Credentials in the authority belong in the request, never echoed
        # into a header that gets logged.
        _, headers = pin_url(
            "http://user:secret@example.test:8080/docs",
            {},
            _validation("example.test"),
        )

        assert headers["Host"] == "example.test:8080"

    def test_a_caller_supplied_host_is_replaced_case_insensitively(self) -> None:
        _, headers = pin_url(
            "http://example.test/docs",
            {"host": "attacker.test", "Accept": "text/html"},
            _validation("example.test"),
        )

        assert headers["Host"] == "example.test"
        assert headers["Accept"] == "text/html"
        assert "host" not in headers

    def test_the_callers_mapping_is_not_mutated(self) -> None:
        supplied = {"host": "attacker.test"}

        pin_url("http://example.test/docs", supplied, _validation("example.test"))

        assert supplied == {"host": "attacker.test"}


@pytest.mark.unit
class TestARejectedUrlDoesNotEchoItsCredentials:
    """The rejection message is logged AND returned to whoever supplied it.

    A blocked URL is exactly where credentials sit unparsed in the authority,
    so echoing it verbatim writes them to the log stream and into the
    persisted turn record.
    """

    async def test_a_disallowed_scheme_is_reported_without_the_userinfo(
        self,
    ) -> None:
        tool = HttpRequestTool(network_policy=NetworkPolicy())

        result = await tool.execute(
            arguments={"url": "ftp://user:hunter2@files.example.test/x"},
        )

        assert result.is_error is True
        assert "hunter2" not in result.content

    async def test_an_unparseable_host_is_reported_without_the_userinfo(
        self,
    ) -> None:
        outcome = await validate_url_host(
            "https://user:hunter2@/no-host",
            NetworkPolicy(),
        )

        assert isinstance(outcome, str)
        assert "hunter2" not in outcome


@pytest.mark.unit
class TestHttpsPinsTheTransportInstead:
    """HTTPS keeps its hostname, so the address has to be pinned elsewhere.

    TLS verifies the certificate against the name, so the URL cannot be
    rewritten to the validated IP. That leaves a second DNS lookup between the
    SSRF verdict and the connection, which an attacker's short-TTL record can
    answer differently: public for the check, private for the connect.
    """

    def test_https_gets_a_pinned_transport(self) -> None:
        transport = _pinned_transport(_validation("example.test", is_https=True))

        assert transport is not None

    async def test_the_pinned_backend_dials_the_validated_address(self) -> None:
        """Construction proves nothing; the connect target is the control.

        A transport that was built and then dialled the hostname anyway would
        pass every assertion above it while leaving the rebinding window
        exactly as open as it was.
        """
        dialled: list[tuple[str, int]] = []
        backend = PinnedDnsBackend(
            _RecordingBackend(dialled),
            hostname="Example.TEST",
            ip="93.184.216.34",
        )

        await backend.connect_tcp("example.test", 443)

        assert dialled == [("93.184.216.34", 443)]

    async def test_another_host_is_refused_rather_than_resolved_unpinned(self) -> None:
        """A name the backend was not pinned to must not be dialled at all.

        Passing it through to the inner backend resolves a name nothing
        checked, which is the rebinding window this transport exists to
        close, and it does so silently: the request succeeds and reads as
        pinned. The caller builds the URL from the validated hostname, so
        reaching here at all means that invariant broke.
        """
        dialled: list[tuple[str, int]] = []
        backend = PinnedDnsBackend(
            _RecordingBackend(dialled),
            hostname="example.test",
            ip="93.184.216.34",
        )

        with pytest.raises(ToolExecutionError):
            await backend.connect_tcp("other.test", 443)

        assert dialled == []

    def test_plain_http_needs_none(self) -> None:
        # The URL was already rewritten to the address, so there is no name
        # left for the connection to re-resolve.
        assert _pinned_transport(_validation("example.test")) is None

    def test_nothing_resolved_means_nothing_to_pin(self) -> None:
        # A literal IP or an allowlisted host never went through DNS.
        validation = _validation("example.test", ips=(), is_https=True)

        assert _pinned_transport(validation) is None

    def test_no_verdict_means_nothing_to_pin(self) -> None:
        assert _pinned_transport(None) is None


@pytest.mark.unit
class TestPinningRewritesOnlyWhatItCan:
    """TLS needs the hostname, so HTTPS is pinned by DNS cache, not by URL."""

    def test_plain_http_connects_to_the_validated_ip(self) -> None:
        url, _ = pin_url(
            "http://example.test:8080/docs",
            {},
            _validation("example.test"),
        )

        assert url == "http://93.184.216.34:8080/docs"

    def test_an_ipv6_target_is_bracketed_in_the_url_too(self) -> None:
        url, _ = pin_url(
            "http://example.test/docs",
            {},
            _validation("example.test", ips=("2001:db8::1",)),
        )

        assert url == "http://[2001:db8::1]/docs"

    def test_https_keeps_the_hostname_for_sni(self) -> None:
        url, _ = pin_url(
            "https://example.test/docs",
            {},
            _validation("example.test", is_https=True),
        )

        assert url == "https://example.test/docs"

    def test_no_resolved_ip_leaves_the_url_alone(self) -> None:
        # A literal IP or an allowlisted host resolves to nothing to pin.
        url, _ = pin_url(
            "http://example.test/docs",
            {},
            _validation("example.test", ips=()),
        )

        assert url == "http://example.test/docs"


@pytest.mark.unit
class TestTheRequestCarriesTheValidatedHostname:
    """One spelling end to end, so the pin matches what httpx dials.

    httpx re-encodes whatever host the URL carries with its own IDNA
    settings, which are not the ones the guard canonicalised with. Handing
    it the A-label takes its pure-ASCII path, where no second encoding
    happens at all, so the name checked and the name connected to are the
    same string rather than two that usually agree.
    """

    def test_https_url_is_rewritten_to_the_validated_hostname(self) -> None:
        url, _ = pin_url(
            "https://exämple.test/docs",
            {},
            _validation("xn--exmple-cua.test", is_https=True),
        )

        assert url == "https://xn--exmple-cua.test/docs"

    def test_the_host_header_carries_the_validated_hostname(self) -> None:
        _, headers = pin_url(
            "https://exämple.test/docs",
            {},
            _validation("xn--exmple-cua.test", is_https=True),
        )

        assert headers["Host"] == "xn--exmple-cua.test"

    def test_a_stated_port_survives_the_rewrite(self) -> None:
        url, headers = pin_url(
            "https://exämple.test:8443/docs",
            {},
            _validation("xn--exmple-cua.test", port=8443, is_https=True),
        )

        assert url == "https://xn--exmple-cua.test:8443/docs"
        assert headers["Host"] == "xn--exmple-cua.test:8443"

    def test_userinfo_is_carried_across(self) -> None:
        """Credentials are part of the request the caller authored."""
        url, headers = pin_url(
            "https://user:secret@exämple.test/docs",
            {},
            _validation("xn--exmple-cua.test", is_https=True),
        )

        assert url == "https://user:secret@xn--exmple-cua.test/docs"
        assert headers["Host"] == "xn--exmple-cua.test"

    def test_the_pinned_backend_accepts_the_rewritten_hostname(self) -> None:
        """The rewrite and the pin must agree, or every request would raise."""
        validation = _validation("xn--exmple-cua.test", is_https=True)
        url, _ = pin_url("https://exämple.test/docs", {}, validation)

        assert urlparse(url).hostname == validation.hostname
