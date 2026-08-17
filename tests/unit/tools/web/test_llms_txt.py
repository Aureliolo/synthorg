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
    discover_llms_txt,
    discovery_notice,
    index_urls_for,
)

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


class TestNotice:
    def test_nothing_discovered_renders_nothing(self) -> None:
        assert discovery_notice("") == ""

    def test_a_discovery_names_the_url(self) -> None:
        notice = discovery_notice(_INDEX)
        assert _INDEX in notice
        assert "index" in notice.lower()
