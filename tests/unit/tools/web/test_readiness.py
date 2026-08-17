"""Unit tests for the web-research readiness verdict.

One owner for "is web search usable": boot decides whether to build the
provider from this, and the dashboard reports it from the same call. These
pin the distinction that makes the verdict worth having: off by choice is not
a fault, while on-but-unconfigured is, because it reads as enabled everywhere
else and answers nothing.
"""

import pytest

from synthorg.tools.web.readiness import (
    WebResearchReadiness,
    WebSearchBlocker,
    resolve_web_research_readiness,
)

pytestmark = pytest.mark.unit


class _StubResolver:
    """Settings reader over a fixed mapping."""

    def __init__(self, **values: object) -> None:
        self._values: dict[str, object] = {
            "web_search_enabled": False,
            "web_search_provider": "",
            "web_search_connection": "",
            "web_fetch_enabled": True,
            "web_fetch_proxy_enabled": False,
        }
        self._values.update(values)

    async def get_bool(self, namespace: str, key: str) -> bool:
        del namespace
        return bool(self._values[key])

    async def get_str(self, namespace: str, key: str) -> str:
        del namespace
        return str(self._values[key])


async def _resolve(
    *,
    catalog: bool = True,
    **values: object,
) -> WebResearchReadiness:
    return await resolve_web_research_readiness(
        _StubResolver(**values),
        has_connection_catalog=catalog,
    )


class TestBlockerOrdering:
    async def test_disabled_is_reported_but_needs_no_action(self) -> None:
        readiness = await _resolve(web_search_enabled=False)
        assert readiness.search_ready is False
        assert readiness.search_blocker is WebSearchBlocker.DISABLED
        assert readiness.needs_operator_action is False
        assert readiness.describe() == ""

    async def test_no_catalog_is_named_first(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="brave",
            web_search_connection="conn",
            catalog=False,
        )
        assert readiness.search_blocker is WebSearchBlocker.NO_CATALOG
        assert readiness.needs_operator_action is True

    async def test_a_missing_provider_is_named(self) -> None:
        readiness = await _resolve(web_search_enabled=True)
        assert readiness.search_blocker is WebSearchBlocker.NO_PROVIDER
        assert "tools.web_search_provider" in readiness.describe()

    async def test_a_missing_connection_is_named(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="brave",
        )
        assert readiness.search_blocker is WebSearchBlocker.NO_CONNECTION
        assert "tools.web_search_connection" in readiness.describe()

    async def test_whitespace_does_not_count_as_configured(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="   ",
        )
        assert readiness.search_blocker is WebSearchBlocker.NO_PROVIDER

    async def test_fully_configured_is_ready(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="ollama",
            web_search_connection="search-conn",
        )
        assert readiness.search_ready is True
        assert readiness.search_blocker is WebSearchBlocker.NONE
        assert readiness.needs_operator_action is False
        assert readiness.provider_id == "ollama"
        assert readiness.connection_name == "search-conn"


class TestFetch:
    async def test_fetch_is_independent_of_search(self) -> None:
        """The local rung needs no credential, so search being off is irrelevant."""
        readiness = await _resolve(web_search_enabled=False, web_fetch_enabled=True)
        assert readiness.fetch_enabled is True
        assert readiness.search_ready is False

    async def test_the_proxy_rung_needs_a_usable_search_binding(self) -> None:
        """It reads the same credential, so it cannot be ready before search."""
        readiness = await _resolve(
            web_search_enabled=True,
            web_fetch_enabled=True,
            web_fetch_proxy_enabled=True,
        )
        assert readiness.fetch_proxy_ready is False

    async def test_the_proxy_rung_is_ready_once_search_is(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="ollama",
            web_search_connection="c",
            web_fetch_enabled=True,
            web_fetch_proxy_enabled=True,
        )
        assert readiness.fetch_proxy_ready is True

    async def test_the_proxy_rung_is_off_when_fetch_is_off(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="ollama",
            web_search_connection="c",
            web_fetch_enabled=False,
            web_fetch_proxy_enabled=True,
        )
        assert readiness.fetch_proxy_ready is False
