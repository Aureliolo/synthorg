"""The guarded-GET primitives shared by ``http_request`` and ``web_fetch``.

``pin_url`` rewrites a plain-HTTP URL onto the IP the SSRF check validated,
which closes the DNS-rebinding window between the check and the connection.
Doing that means the connection no longer carries the hostname, so the ``Host``
header becomes the only thing telling the origin which site was asked for.
"""

import pytest

from synthorg.tools.network_validator import DnsValidationOk
from synthorg.tools.web._guarded_fetch import pin_url


def _validation(
    hostname: str,
    *,
    ips: tuple[str, ...] = ("93.184.216.34",),
    is_https: bool = False,
) -> DnsValidationOk:
    return DnsValidationOk(hostname=hostname, resolved_ips=ips, is_https=is_https)


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
