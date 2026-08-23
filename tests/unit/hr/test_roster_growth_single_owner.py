"""The roster grows through one gated path and no other.

Adding an agent commits the organisation to ongoing spend and puts a new
actor inside the approval boundary, so "should the roster grow" has exactly
one owner: a :class:`~synthorg.hr.hiring_service.HiringService` request that
an operator approves. The gate-role reconciler
(``engine/review_staffing/reconciler.py``) reaches that owner and nothing
else.

A second, autonomous grower is the failure this guards: an evaluator that
decides on its own signals and hires behind the same approval store reads
as a peer of the reconciler while answering a question the reconciler
already owns. These assert the surface carries no such second path, at
every layer one could reappear at: the package tree, the settings a
namespace offers, the REST controllers a feature mounts, and the MCP tools
an agent can reach.
"""

import importlib
import pkgutil

import pytest
from litestar import Controller

import synthorg.hr
import synthorg.settings.definitions  # noqa: F401 -- triggers registration
from synthorg._core.features import ControllerRegistration
from synthorg.hr.feature import FEATURE
from synthorg.meta.mcp.domains import build_full_registry
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit

#: Substrings that name an autonomous roster-sizing surface. A hit at any
#: layer means something other than the hiring pipeline is answering "should
#: the roster grow", which is the single-owner breach this module exists for.
_AUTONOMOUS_SIZING_TERMS = ("scaling", "autoscale", "auto_scale")


def _names_autonomous_sizing(value: str) -> bool:
    """Whether *value* names an autonomous roster-sizing surface.

    Returns:
        ``True`` when the lowercased value contains any sizing term.
    """
    lowered = value.lower()
    return any(term in lowered for term in _AUTONOMOUS_SIZING_TERMS)


def test_hr_ships_no_autonomous_sizing_package() -> None:
    """No submodule of ``synthorg.hr`` implements a second grower."""
    offenders = [
        module.name
        for module in pkgutil.iter_modules(synthorg.hr.__path__)
        if _names_autonomous_sizing(module.name)
    ]
    assert offenders == [], (
        f"synthorg.hr ships an autonomous roster-sizing package: {offenders}. "
        "Roster growth is owned by HiringService behind the approval gate."
    )


def test_hr_namespace_offers_no_autonomous_sizing_setting() -> None:
    """No ``hr`` setting tunes a second grower.

    A knob is the operator-visible half of a decision. One that tunes
    autonomous sizing advertises an owner that must not exist.
    """
    offenders = [
        defn.key
        for defn in get_registry().list_all()
        if defn.namespace is SettingNamespace.HR and _names_autonomous_sizing(defn.key)
    ]
    assert offenders == [], (
        f"hr namespace offers autonomous roster-sizing settings: {offenders}"
    )


def _controller_name(entry: type[Controller] | ControllerRegistration) -> str:
    """Name the controller a manifest entry mounts.

    A manifest may list a bare class or wrap it in a registration that adds
    a mount point and a predicate, so both shapes reduce to the class name.

    Returns:
        The controller class name.
    """
    if isinstance(entry, ControllerRegistration):
        return entry.controller.__name__
    return entry.__name__


def test_hr_feature_mounts_no_autonomous_sizing_controller() -> None:
    """The HR manifest mounts no REST surface for a second grower."""
    offenders = [
        name
        for name in (_controller_name(entry) for entry in FEATURE.controllers)
        if _names_autonomous_sizing(name)
    ]
    assert offenders == [], (
        f"HR feature mounts autonomous roster-sizing controllers: {offenders}"
    )
    ghosts = [
        symbol
        for symbol in FEATURE.ghost_wired_symbols
        if _names_autonomous_sizing(symbol)
    ]
    assert ghosts == [], f"HR declares autonomous-sizing ghost symbols: {ghosts}"


def test_no_mcp_tool_reaches_an_autonomous_sizer() -> None:
    """No agent-reachable tool triggers or reports a second grower.

    The MCP surface is what an ELEVATED agent can call, so a sizing tool
    there would let an agent grow the roster on a path the reconciler does
    not own.

    Scanned whole rather than narrowed to an HR-shaped prefix, deliberately.
    The tools this replaces lived under ``scaling`` and ``signals``, neither
    of them HR-named, so a prefix filter would have been blind to exactly
    what it is here to catch. The cost is that a future unrelated tool (an
    image or chart scaler) trips it; the fix then is to name that tool here
    as a known non-sizer, never to drop the term.
    """
    registry = build_full_registry()
    offenders = sorted(
        name for name in registry.get_names() if _names_autonomous_sizing(name)
    )
    assert offenders == [], f"MCP exposes autonomous roster-sizing tools: {offenders}"


def test_the_gated_hiring_owner_is_reachable() -> None:
    """The one legitimate owner still exists.

    The complement of every assertion above: a suite that only checks for
    absence passes just as happily when the real path has been deleted too.
    """
    hiring = importlib.import_module("synthorg.hr.hiring_service")
    assert hasattr(hiring, "HiringService")
    staffing = importlib.import_module("synthorg.engine.review_staffing.hiring_pass")
    assert hasattr(staffing, "ensure_hire_open")
    assert hasattr(staffing, "finish_approved_hires")
