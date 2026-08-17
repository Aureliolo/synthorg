# module-kind: code
"""Render search filters into one provider's own request vocabulary.

A recency window and a domain restriction mean the same thing everywhere and
are spelled differently everywhere, so the caller asks in neutral terms and
the preset decides how (or whether) to say it. A filter the selected provider
cannot express is NAMED rather than dropped: results that were never filtered
but look filtered are worse than no filter at all, because the caller stops
checking the dates.
"""

from datetime import datetime, timedelta
from typing import Final

from pydantic import JsonValue

from synthorg.tools.web.providers.presets import (
    RECENCY_WINDOW_DAYS,
    SearchProviderPreset,
)
from synthorg.tools.web.web_search import SearchFilters

_ISO_DATE_FORMAT: Final[str] = "%Y-%m-%d"


def build_filter_params(
    preset: SearchProviderPreset,
    filters: SearchFilters | None,
    *,
    now: datetime,
) -> dict[str, JsonValue]:
    """Render *filters* into the keys *preset* declares.

    Args:
        preset: The selected provider's contract.
        filters: What the caller asked for, or ``None``.
        now: Current time, used to turn a window into an absolute date for a
            provider that takes one.

    Returns:
        The request keys to merge, empty when nothing applies.
    """
    if filters is None or filters.is_empty:
        return {}
    params: dict[str, JsonValue] = {}
    recency_value = _render_recency(preset, filters.recency, now=now)
    if recency_value is not None and preset.freshness_key is not None:
        params[preset.freshness_key] = recency_value
    if filters.include_domains and preset.include_domains_key is not None:
        params[preset.include_domains_key] = _render_domains(
            preset, filters.include_domains
        )
    if filters.exclude_domains and preset.exclude_domains_key is not None:
        params[preset.exclude_domains_key] = _render_domains(
            preset, filters.exclude_domains
        )
    return params


def unsupported_filter_names(
    preset: SearchProviderPreset,
    filters: SearchFilters | None,
) -> tuple[str, ...]:
    """Name every requested filter *preset* cannot express.

    Returns:
        The filter names that will not be applied, in declaration order.
    """
    if filters is None or filters.is_empty:
        return ()
    missing: list[str] = []
    if filters.recency is not None and not preset.supports_recency:
        missing.append("recency")
    if filters.include_domains and preset.include_domains_key is None:
        missing.append("include_domains")
    if filters.exclude_domains and preset.exclude_domains_key is None:
        missing.append("exclude_domains")
    return tuple(missing)


def _render_recency(
    preset: SearchProviderPreset,
    recency: str | None,
    *,
    now: datetime,
) -> str | None:
    """Spell *recency* the way *preset* expects, or ``None`` if it cannot.

    Returns:
        The provider's own token, an absolute earliest-publication date, or
        ``None`` when this provider has no date filter or the window is
        unknown to it.
    """
    if recency is None or not preset.supports_recency:
        return None
    if preset.freshness_style == "iso_date":
        days = RECENCY_WINDOW_DAYS.get(recency)
        if days is None:
            return None
        return (now - timedelta(days=days)).strftime(_ISO_DATE_FORMAT)
    return preset.freshness_values.get(recency)


def _render_domains(
    preset: SearchProviderPreset,
    domains: tuple[str, ...],
) -> JsonValue:
    """Render a hostname list as the array or CSV the provider takes.

    Returns:
        A JSON array, or a comma-joined string for a CSV-style provider.
    """
    if preset.domains_as_csv:
        return ",".join(domains)
    return list(domains)


__all__ = [
    "build_filter_params",
    "unsupported_filter_names",
]
