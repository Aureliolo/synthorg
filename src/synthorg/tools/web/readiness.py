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
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.http_vendor import METADATA_KEY_VENDOR
from synthorg.integrations.connections.models import Connection
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.web import (
    WEB_RESEARCH_CONNECTION_SCAN_FAILED,
    WEB_RESEARCH_READINESS,
)
from synthorg.tools.web.providers.fetch_presets import get_fetch_preset

logger = get_logger(__name__)

_TOOLS_NS: Final[str] = "tools"


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


@runtime_checkable
class ConnectionLister(Protocol):
    """The slice of the connection catalog this decision needs."""

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List every configured connection."""
        ...


class WebResearchReadiness(BaseModel):
    """What the operator has and has not configured for web research.

    Attributes:
        search_ready: Whether a search provider can be built and bound.
        search_blocker: The condition stopping it, or ``NONE``.
        provider_id: The selected vendor, empty when unset.
        connection_name: The bound connection, empty when unset.
        fetch_enabled: Whether the fetch tool is offered at all.
        fetch_proxy_ready: Whether the vendor-reader rung can be built. Needs
            the search binding to be usable AND the bound vendor to ship a
            reader, which not every search vendor does; it is judged on the
            same preset lookup the wiring builds from, so the two cannot
            report different answers.
        reusable_connections: Names of connections the operator has ALREADY
            saved whose vendor matches the selected provider. Reported so a
            blocked setup can point at a credential that already exists rather
            than asking for one again; nothing selects them automatically,
            because binding a connection to a second purpose is the operator's
            call, not a default.
        notice_dismissed: Whether the operator asked not to be told about this
            again, which a deployment happy with local-only fetch will.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    search_ready: bool
    search_blocker: WebSearchBlocker
    provider_id: str = ""
    connection_name: str = ""
    fetch_enabled: bool = False
    fetch_proxy_ready: bool = False
    reusable_connections: tuple[str, ...] = ()
    notice_dismissed: bool = False

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

    @property
    def should_notify(self) -> bool:
        """Whether the dashboard should raise this with the operator.

        A deployment that is content with local-only page reading is not
        misconfigured, so a dismissal is respected rather than re-litigated
        on every page load.
        """
        return self.needs_operator_action and not self.notice_dismissed

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
    connections: ConnectionLister | None,
) -> WebResearchReadiness:
    """Decide whether web research is usable, and name what is missing.

    Args:
        resolver: The settings resolver.
        connections: The connection catalog, or ``None`` when integrations are
            off and therefore no credential can be brokered at all.

    Returns:
        The verdict, with the first unmet condition named.
    """
    enabled = await resolver.get_bool(_TOOLS_NS, "web_search_enabled")
    provider_id = (await resolver.get_str(_TOOLS_NS, "web_search_provider")).strip()
    connection = (await resolver.get_str(_TOOLS_NS, "web_search_connection")).strip()
    fetch_enabled = await resolver.get_bool(_TOOLS_NS, "web_fetch_enabled")
    proxy_enabled = await resolver.get_bool(_TOOLS_NS, "web_fetch_proxy_enabled")
    dismissed = await resolver.get_bool(_TOOLS_NS, "web_search_notice_dismissed")

    blocker = _first_blocker(
        enabled=enabled,
        has_catalog=connections is not None,
        provider_id=provider_id,
        connection=connection,
    )
    ready = blocker is WebSearchBlocker.NONE
    verdict = WebResearchReadiness(
        search_ready=ready,
        search_blocker=blocker,
        provider_id=provider_id,
        connection_name=connection,
        fetch_enabled=fetch_enabled,
        # The proxy rung rides the search connection, so it needs search to be
        # usable AND the bound vendor to ship a reader at all. Judging it on
        # search alone reported it ready for a vendor that sells no reader,
        # while the wiring that actually builds the ladder silently left it out.
        fetch_proxy_ready=(
            fetch_enabled
            and proxy_enabled
            and ready
            and get_fetch_preset(provider_id) is not None
        ),
        reusable_connections=await _reusable_connections(
            connections,
            provider_id=provider_id,
            already_bound=connection,
        ),
        notice_dismissed=dismissed,
    )
    # Logged here rather than only at boot: this same call answers every
    # dashboard poll, so a verdict that changes mid-process (an operator fixes
    # the binding, or breaks it again) otherwise leaves no trace after the one
    # startup snapshot.
    logger.debug(
        WEB_RESEARCH_READINESS,
        service="web_search",
        blocker=verdict.search_blocker.value,
        search_ready=verdict.search_ready,
        fetch_enabled=verdict.fetch_enabled,
        fetch_proxy_ready=verdict.fetch_proxy_ready,
    )
    return verdict


async def _reusable_connections(
    connections: ConnectionLister | None,
    *,
    provider_id: str,
    already_bound: str,
) -> tuple[str, ...]:
    """Name saved connections whose vendor matches the selected provider.

    An operator who added a vendor connection for anything else already holds
    the credential web search needs, and asking for it a second time is how a
    setup stalls on a key that is sitting right there.

    Returns:
        The matching connection names, empty when nothing matches or the
        selected provider is still unset. Never includes the bound one, since
        suggesting what is already chosen is noise.
    """
    if connections is None or not provider_id:
        return ()
    try:
        saved = await connections.list_all()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- a setup convenience read. A catalog blip
        # must not turn "here is your existing key" into a failed readiness
        # check that hides the blocker it was meant to help fix.
        reraise_critical(exc)
        logger.warning(
            WEB_RESEARCH_CONNECTION_SCAN_FAILED,
            service="web_search",
            note="could not list connections; no reuse suggested",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
    return tuple(
        connection.name
        for connection in saved
        if connection.metadata.get(METADATA_KEY_VENDOR, "") == provider_id
        and connection.name != already_bound
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
