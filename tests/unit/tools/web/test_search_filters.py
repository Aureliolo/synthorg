"""Unit tests for rendering search filters into a provider's vocabulary.

The rule under test: a filter the selected provider cannot express is NAMED,
never dropped. Results that were never filtered but look filtered are worse
than no filter, because the caller stops checking the dates itself.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.http_vendor import HttpVendorPreset
from synthorg.tools.web.providers._filters import (
    build_filter_params,
    unsupported_filter_names,
)
from synthorg.tools.web.providers.presets import SearchProviderPreset
from synthorg.tools.web.web_search import SearchFilters

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _vendor() -> HttpVendorPreset:
    return HttpVendorPreset(
        id=NotBlankStr("example-provider"),
        label=NotBlankStr("Example"),
        base_url=NotBlankStr("https://search.example-provider.test/search"),
        auth_header=NotBlankStr("X-Example-Token"),
    )


_KEYWORD = SearchProviderPreset(
    vendor=_vendor(),
    method="POST",
    query_key="query",
    max_results_cap=20,
    results_path=("results",),
    snippet_key="content",
    freshness_key="window",
    freshness_values={"day": "d", "week": "w", "month": "m", "year": "y"},
    include_domains_key="only",
    exclude_domains_key="never",
)

_ISO_DATE = SearchProviderPreset(
    vendor=_vendor(),
    method="POST",
    query_key="query",
    max_results_cap=20,
    results_path=("results",),
    snippet_key="content",
    freshness_key="published_after",
    freshness_style="iso_date",
)

_NO_FILTERS = SearchProviderPreset(
    vendor=_vendor(),
    method="POST",
    query_key="query",
    max_results_cap=10,
    results_path=("results",),
    snippet_key="content",
)


class TestKeywordStyle:
    def test_window_uses_the_providers_own_token(self) -> None:
        params = build_filter_params(_KEYWORD, SearchFilters(recency="week"), now=_NOW)
        assert params == {"window": "w"}

    def test_domains_render_as_arrays(self) -> None:
        params = build_filter_params(
            _KEYWORD,
            SearchFilters(include_domains=("docs.example.test",)),
            now=_NOW,
        )
        assert params == {"only": ["docs.example.test"]}

    def test_exclusions_use_their_own_key(self) -> None:
        params = build_filter_params(
            _KEYWORD,
            SearchFilters(exclude_domains=("spam.example.test",)),
            now=_NOW,
        )
        assert params == {"never": ["spam.example.test"]}


class TestIsoDateStyle:
    def test_window_becomes_an_absolute_earliest_date(self) -> None:
        """Days-per-window is arithmetic, not something a vendor defines."""
        params = build_filter_params(_ISO_DATE, SearchFilters(recency="week"), now=_NOW)
        assert params == {"published_after": "2026-08-10"}

    def test_a_year_window_walks_back_a_year(self) -> None:
        params = build_filter_params(_ISO_DATE, SearchFilters(recency="year"), now=_NOW)
        assert params == {"published_after": "2025-08-17"}


class TestUnsupportedAreNamed:
    def test_a_provider_without_dates_reports_recency(self) -> None:
        names = unsupported_filter_names(
            _NO_FILTERS, SearchFilters(recency="day"), now=_NOW
        )
        assert names == ("recency",)

    def test_a_provider_without_dates_sends_nothing_for_it(self) -> None:
        params = build_filter_params(
            _NO_FILTERS, SearchFilters(recency="day"), now=_NOW
        )
        assert params == {}

    def test_every_unsupported_filter_is_listed(self) -> None:
        names = unsupported_filter_names(
            _NO_FILTERS,
            SearchFilters(
                recency="day",
                include_domains=("a.test",),
                exclude_domains=("b.test",),
            ),
            now=_NOW,
        )
        assert names == ("recency", "include_domains", "exclude_domains")

    def test_a_supported_filter_is_not_reported(self) -> None:
        assert (
            unsupported_filter_names(_KEYWORD, SearchFilters(recency="day"), now=_NOW)
            == ()
        )

    def test_a_domain_only_provider_reports_only_recency(self) -> None:
        names = unsupported_filter_names(
            _ISO_DATE,
            SearchFilters(recency="day", include_domains=("a.test",)),
            now=_NOW,
        )
        assert names == ("include_domains",)

    def test_a_window_outside_the_providers_vocabulary_is_reported(self) -> None:
        """Supporting recency and supporting THIS window are two claims.

        A preset whose token map lacks the requested window renders nothing
        for it, so the request goes out unfiltered. Answering from the
        capability flag alone would call that applied, and the caller then
        stops checking the dates on results that were never filtered.
        """
        narrow = SearchProviderPreset(
            vendor=_vendor(),
            method="POST",
            query_key="query",
            max_results_cap=20,
            results_path=("results",),
            snippet_key="content",
            freshness_key="window",
            freshness_values={"day": "d"},
        )
        asked = SearchFilters(recency="year")

        assert build_filter_params(narrow, asked, now=_NOW) == {}
        assert unsupported_filter_names(narrow, asked, now=_NOW) == ("recency",)


class TestNothingRequested:
    @pytest.mark.parametrize("filters", [None, SearchFilters()])
    def test_no_params_and_no_complaints(
        self,
        filters: SearchFilters | None,
    ) -> None:
        assert build_filter_params(_KEYWORD, filters, now=_NOW) == {}
        assert unsupported_filter_names(_NO_FILTERS, filters, now=_NOW) == ()
