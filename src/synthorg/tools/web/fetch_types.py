# module-kind: code
"""The contracts every ``web_fetch`` rung and its wiring agree on.

A leaf, importing no rung and no tool, because the direction of every other
import in this feature runs towards it: a provider states what it returns, the
boot wiring states what it assembled, and the tool consumes both. Declaring
them beside the tool instead makes each provider import the tool module, which
is a cycle the render rung already had to work around by typing its browser
handle as ``object`` and deciding at runtime.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.network_validator import NetworkPolicy
from synthorg.tools.web.web_search import WebSearchProvider


class FetchBackend(StrEnum):
    """Which rung served, or is being asked to serve, a fetch."""

    LOCAL = "local"
    PROXY = "proxy"
    RENDER = "render"


class FetchedPage(BaseModel):
    """One page read as markdown.

    Attributes:
        url: The URL as requested.
        final_url: Where the read actually landed, when the backend reports it.
        title: Page title, empty when the page declares none.
        markdown: Extracted content; empty when nothing readable survived.
        backend: The rung that produced this.
        truncated: Whether the content was cut to fit the character budget.
        links: Outbound links, only when the backend returns them.
        hidden_content_detected: Whether the page carried substantial text
            invisible to a reader. That text is stripped before extraction, so
            this is an alarm rather than a hazard: a documentation page has no
            reason to hide prose from the human and show it to the machine.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    url: NotBlankStr
    final_url: str = ""
    title: str = ""
    markdown: str
    backend: FetchBackend
    truncated: bool = False
    links: tuple[str, ...] = ()
    hidden_content_detected: bool = False


@runtime_checkable
class WebFetchProvider(Protocol):
    """One rung of the fetch ladder."""

    @property
    def backend(self) -> FetchBackend:
        """Which rung this provider is."""
        ...

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What this rung offers beyond markdown, for the agent to weigh."""
        ...

    async def fetch(self, url: str) -> FetchedPage:
        """Read *url* and return it as markdown."""
        ...


class FetchBudget(BaseModel):
    """How much of a response a rung accepts.

    The two ceilings travel together: bytes bound what is read off the wire,
    characters bound what reaches the agent, and every rung needs both. They
    are one argument because they are one decision, and because a rung
    configured with one and not the other is not a state an operator can
    express.

    Attributes:
        max_response_bytes: Hard ceiling on the body read from the wire.
        char_budget: Ceiling on the markdown handed back.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_response_bytes: int = Field(gt=0)
    char_budget: int = Field(gt=0)


@runtime_checkable
class RenderedPageSource(Protocol):
    """The slice of the browser tool the render rung drives."""

    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Run one browser operation."""
        ...


class WebFetchRungs(BaseModel):
    """The ladder resolved from settings, plus what the tool needs to build it.

    The render rung is declared here but completed in the tool factory, which
    is the first place the browser tool exists; boot has the settings but not
    the sandbox.

    Attributes:
        providers: The rungs already built, keyed by backend.
        discover_docs_index: Whether a fetch also probes for ``llms.txt``.
        render_enabled: Whether the operator asked for the rendered rung.
        char_budget: Markdown ceiling, needed to finish the render rung.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    providers: dict[FetchBackend, WebFetchProvider]
    discover_docs_index: bool = True
    render_enabled: bool = False
    char_budget: int = Field(gt=0)


class WebToolsWiring(BaseModel):
    """Everything the tool factory needs to build the web cohort.

    Grouped because these five travel together through every layer of the
    factory and passing them individually pushed the cohort builder over the
    argument cap.

    Attributes:
        network_policy: SSRF policy shared by every web tool.
        request_timeout: Per-request timeout for the plain HTTP tool.
        search_provider: The bound search backend, or ``None`` when unset.
        fetch_rungs: The resolved fetch ladder, or ``None`` when off.
        render_source: The browser tool backing the rendered rung, or ``None``.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    network_policy: NetworkPolicy | None = None
    request_timeout: float = Field(gt=0)
    search_provider: WebSearchProvider | None = None
    fetch_rungs: WebFetchRungs | None = None
    render_source: RenderedPageSource | None = None


__all__ = [
    "FetchBackend",
    "FetchBudget",
    "FetchedPage",
    "RenderedPageSource",
    "WebFetchProvider",
    "WebFetchRungs",
    "WebToolsWiring",
]
