# module-kind: code
"""Whether web research is usable, and if not, exactly what is missing.

One owner for the question. Boot consults this to decide whether to build the
search provider, and the dashboard's capabilities surface reports the same
verdict, so the operator is never told a feature is on while the runtime has
quietly declined to build it.

The reason is a named condition rather than free text: "enabled but nothing is
configured" and "enabled and configured" are different states, and only the
first is worth interrupting an operator about.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

_TOOLS_NS: str = "tools"


class WebSearchBlocker(StrEnum):
    """Why web search is not serving requests."""

    NONE = "none"
    DISABLED = "disabled"
    NO_PROVIDER = "no_provider"
    NO_CONNECTION = "no_connection"
    NO_CATALOG = "no_catalog"


@runtime_checkable
class SettingsReader(Protocol):
    """The slice of the settings resolver this decision needs."""

    async def get_bool(self, namespace: str, key: str) -> bool:
        """Read a boolean setting."""
        ...

    async def get_str(self, namespace: str, key: str) -> str:
        """Read a string setting."""
        ...


class WebResearchReadiness(BaseModel):
    """What the operator has and has not configured for web research.

    Attributes:
        search_ready: Whether a search provider can be built and bound.
        search_blocker: The condition stopping it, or ``NONE``.
        provider_id: The selected vendor, empty when unset.
        connection_name: The bound connection, empty when unset.
        fetch_enabled: Whether the fetch tool is offered at all.
        fetch_proxy_ready: Whether the vendor-reader rung can be built.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    search_ready: bool
    search_blocker: WebSearchBlocker
    provider_id: str = ""
    connection_name: str = ""
    fetch_enabled: bool = False
    fetch_proxy_ready: bool = False

    @property
    def needs_operator_action(self) -> bool:
        """Whether an operator turned search on and left it unusable.

        Off-by-choice is not a problem to report; on-but-unconfigured is,
        because it looks enabled everywhere else and answers nothing.
        """
        return self.search_blocker not in {
            WebSearchBlocker.NONE,
            WebSearchBlocker.DISABLED,
        }

    def describe(self) -> str:
        """Render the blocker as a sentence naming what to set.

        Returns:
            An operator-facing explanation, empty when nothing is wrong.
        """
        return _BLOCKER_MESSAGES.get(self.search_blocker, "")


_BLOCKER_MESSAGES: dict[WebSearchBlocker, str] = {
    WebSearchBlocker.NO_PROVIDER: (
        "Web search is enabled but no provider is selected. Set"
        " tools.web_search_provider to the vendor whose key you hold."
    ),
    WebSearchBlocker.NO_CONNECTION: (
        "Web search is enabled but no connection is bound. Set"
        " tools.web_search_connection to a generic_http connection holding"
        " the provider's API key."
    ),
    WebSearchBlocker.NO_CATALOG: (
        "Web search is enabled but the integrations subsystem is off, so no"
        " connection can be resolved to broker the provider's API key."
    ),
}


async def resolve_web_research_readiness(
    resolver: SettingsReader,
    *,
    has_connection_catalog: bool,
) -> WebResearchReadiness:
    """Decide whether web research is usable, and name what is missing.

    Args:
        resolver: The settings resolver.
        has_connection_catalog: Whether a connection catalog is wired, without
            which no credential can be brokered.

    Returns:
        The verdict, with the first unmet condition named.
    """
    enabled = await resolver.get_bool(_TOOLS_NS, "web_search_enabled")
    provider_id = (await resolver.get_str(_TOOLS_NS, "web_search_provider")).strip()
    connection = (await resolver.get_str(_TOOLS_NS, "web_search_connection")).strip()
    fetch_enabled = await resolver.get_bool(_TOOLS_NS, "web_fetch_enabled")
    proxy_enabled = await resolver.get_bool(_TOOLS_NS, "web_fetch_proxy_enabled")

    blocker = _first_blocker(
        enabled=enabled,
        has_catalog=has_connection_catalog,
        provider_id=provider_id,
        connection=connection,
    )
    ready = blocker is WebSearchBlocker.NONE
    return WebResearchReadiness(
        search_ready=ready,
        search_blocker=blocker,
        provider_id=provider_id,
        connection_name=connection,
        fetch_enabled=fetch_enabled,
        fetch_proxy_ready=fetch_enabled and proxy_enabled and ready,
    )


def _first_blocker(
    *,
    enabled: bool,
    has_catalog: bool,
    provider_id: str,
    connection: str,
) -> WebSearchBlocker:
    """Name the first unmet condition, in the order an operator fixes them.

    Returns:
        The blocking condition, or ``NONE`` when search can be built.
    """
    if not enabled:
        return WebSearchBlocker.DISABLED
    if not has_catalog:
        return WebSearchBlocker.NO_CATALOG
    if not provider_id:
        return WebSearchBlocker.NO_PROVIDER
    if not connection:
        return WebSearchBlocker.NO_CONNECTION
    return WebSearchBlocker.NONE


__all__ = [
    "SettingsReader",
    "WebResearchReadiness",
    "WebSearchBlocker",
    "resolve_web_research_readiness",
]
