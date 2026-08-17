"""Unit tests for the web-research readiness verdict.

One owner for "is web search usable": boot decides whether to build the
provider from this, and the dashboard reports it from the same call. These
pin the distinction that makes the verdict worth having: off by choice is not
a fault, while on-but-unconfigured is, because it reads as enabled everywhere
else and answers nothing.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.http_vendor import METADATA_KEY_VENDOR
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
)
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
            "web_search_notice_dismissed": False,
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


class _StubCatalog:
    """Connection lister over fixed (name, vendor) pairs."""

    def __init__(self, *pairs: tuple[str, str], error: Exception | None = None) -> None:
        self._pairs = pairs
        self._error = error

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        del limit, offset
        if self._error is not None:
            raise self._error
        return tuple(
            Connection(
                name=NotBlankStr(name),
                connection_type=ConnectionType.GENERIC_HTTP,
                auth_method=AuthMethod.API_KEY,
                metadata={METADATA_KEY_VENDOR: vendor},
            )
            for name, vendor in self._pairs
        )


_NO_CATALOG = object()


async def _resolve(
    *,
    catalog: _StubCatalog | object | None = None,
    **values: object,
) -> WebResearchReadiness:
    """Resolve with a stub catalog; pass ``catalog=_NO_CATALOG`` for none."""
    if catalog is _NO_CATALOG:
        connections = None
    elif isinstance(catalog, _StubCatalog):
        connections = catalog
    else:
        connections = _StubCatalog()
    return await resolve_web_research_readiness(
        _StubResolver(**values),
        connections=connections,
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
            catalog=_NO_CATALOG,
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


class TestReusableConnections:
    """An operator who already saved the vendor's key should be told."""

    async def test_a_matching_vendor_connection_is_offered(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="ollama",
            catalog=_StubCatalog(("my-ollama", "ollama")),
        )
        assert readiness.reusable_connections == ("my-ollama",)

    async def test_a_different_vendor_is_not_offered(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="ollama",
            catalog=_StubCatalog(("my-brave", "brave")),
        )
        assert readiness.reusable_connections == ()

    async def test_the_already_bound_connection_is_not_re_suggested(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_provider="ollama",
            web_search_connection="my-ollama",
            catalog=_StubCatalog(("my-ollama", "ollama"), ("spare", "ollama")),
        )
        assert readiness.reusable_connections == ("spare",)

    async def test_nothing_is_suggested_before_a_provider_is_chosen(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            catalog=_StubCatalog(("my-ollama", "ollama")),
        )
        assert readiness.reusable_connections == ()

    async def test_a_catalog_failure_does_not_break_the_verdict(self) -> None:
        """A convenience read must not hide the blocker it exists to help fix."""
        readiness = await _resolve(
            web_search_enabled=True,
            catalog=_StubCatalog(error=RuntimeError("catalog down")),
        )
        assert readiness.reusable_connections == ()
        assert readiness.search_blocker is WebSearchBlocker.NO_PROVIDER


class TestDismissal:
    """Local-only-by-choice is not a misconfiguration to re-raise forever."""

    async def test_an_unconfigured_search_notifies_by_default(self) -> None:
        readiness = await _resolve(web_search_enabled=True)
        assert readiness.should_notify is True

    async def test_a_dismissal_silences_the_notice(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_notice_dismissed=True,
        )
        assert readiness.needs_operator_action is True
        assert readiness.should_notify is False

    async def test_dismissal_does_not_claim_search_works(self) -> None:
        readiness = await _resolve(
            web_search_enabled=True,
            web_search_notice_dismissed=True,
        )
        assert readiness.search_ready is False


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
