# module-kind: declarative
"""Type shapes for the in-sandbox browser executor (host-side typing only).

These TypedDicts describe the JSON payloads the standalone ``_executor``
script exchanges with the host. They live in a separate module so the
executor stays free of ``synthorg`` imports at sandbox runtime: the
executor references them only under ``TYPE_CHECKING``.
"""

from typing import Literal, NotRequired, TypedDict


class BrowserPayload(TypedDict):
    """Decoded ``BROWSER_TOOL_ARGS_JSON`` payload (host-validated shape)."""

    operation: str
    url: str
    wait_condition: NotRequired[Literal["load", "domcontentloaded", "networkidle"]]
    viewport_width: NotRequired[int]
    viewport_height: NotRequired[int]
    full_page: NotRequired[bool]
    navigation_timeout_seconds: NotRequired[float]
    launch_timeout_seconds: NotRequired[float]
    screenshot_path: NotRequired[str]
    axe_script_path: NotRequired[str]
    min_impact: NotRequired[str]
    axe_version: NotRequired[str]


class Violation(TypedDict):
    """Normalised axe-core violation row (in-container JSON shape)."""

    rule_id: str
    impact: str
    description: str
    help_url: str | None
    affected_nodes: int
