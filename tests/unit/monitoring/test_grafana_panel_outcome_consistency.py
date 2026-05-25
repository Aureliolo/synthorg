# mypy: disable-error-code="explicit-any"
"""Invariant: Grafana panel descriptions stay in lockstep with VALID_*.

The Grafana dashboard at ``monitoring/grafana/synthorg-overview.json``
documents each outcome-labelled metric's outcome list inside the
panel ``description``. If a new outcome is added to the allowlist
constants in ``observability/prometheus_labels.py``, the panel
description MUST be updated too or operators are misled.

This test pins each outcome-labelled panel to the matching
``VALID_*`` frozenset so a one-sided update fails the build.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from synthorg.observability.prometheus_labels import (
    VALID_APPROVAL_OUTCOMES,
    VALID_BLUEPRINT_OUTCOMES,
    VALID_ESCALATION_OUTCOMES,
)

pytestmark = pytest.mark.unit

_DASHBOARD_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "monitoring"
    / "grafana"
    / "synthorg-overview.json"
)

# Map ``metric_name`` -> ``frozenset`` allowlist for each panel we pin.
_METRIC_TO_ALLOWLIST: dict[str, frozenset[str]] = {
    "synthorg_approval_decisions_total": VALID_APPROVAL_OUTCOMES,
    "synthorg_escalation_outcomes_total": VALID_ESCALATION_OUTCOMES,
    "synthorg_blueprint_instantiations_total": VALID_BLUEPRINT_OUTCOMES,
}


def _iter_panels(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Recursively yield every panel (including nested rows)."""
    panels: list[dict[str, Any]] = []

    def _walk(node: dict[str, Any]) -> None:
        for panel in node.get("panels", []) or []:
            panels.append(panel)
            if "panels" in panel:
                _walk(panel)

    _walk(dashboard)
    return panels


def _panel_metric(panel: dict[str, Any]) -> str | None:
    """Return the first ``synthorg_*`` metric name referenced by a target expr."""
    for target in panel.get("targets", []) or []:
        expr = target.get("expr") or ""
        match = re.search(r"(synthorg_[a-zA-Z0-9_]+)", expr)
        if match:
            return match.group(1)
    return None


_ALL_KNOWN_OUTCOMES: frozenset[str] = (
    VALID_APPROVAL_OUTCOMES | VALID_ESCALATION_OUTCOMES | VALID_BLUEPRINT_OUTCOMES
)


def _outcomes_in_description(description: str, allowlist: frozenset[str]) -> set[str]:
    """Return outcome-like tokens that appear in the panel description.

    Considers the *union* of every project allowlist so a stale token
    that was dropped from a specific allowlist but is still referenced
    in the panel description surfaces in the ``stale = found -
    allowlist`` check downstream. A previous version of this helper
    iterated only over the per-panel allowlist, which made the stale
    check definitionally empty.

    Whole-word matching (``\\b...\\b``) prevents a short token like
    ``"success"`` from matching ``"successful"``.
    """
    found: set[str] = set()
    for token in _ALL_KNOWN_OUTCOMES:
        pattern = re.compile(rf"\b{re.escape(token)}\b")
        if pattern.search(description):
            found.add(token)
    # Preserve the original signature: the *minimum* membership claim
    # callers rely on is "what allowlist tokens appear here?". The
    # set returned remains a superset of that, and the union-mode
    # discovery is what lets the stale check have teeth.
    del allowlist  # union of all allowlists drives detection now
    return found


class TestGrafanaPanelOutcomeConsistency:
    def test_dashboard_file_exists(self) -> None:
        assert _DASHBOARD_PATH.exists(), (
            f"expected Grafana dashboard at {_DASHBOARD_PATH}"
        )

    def test_each_pinned_panel_description_matches_allowlist(self) -> None:
        dashboard = json.loads(_DASHBOARD_PATH.read_text(encoding="utf-8"))
        panels = _iter_panels(dashboard)
        for metric, allowlist in _METRIC_TO_ALLOWLIST.items():
            matching = [p for p in panels if _panel_metric(p) == metric]
            assert matching, (
                f"no Grafana panel found whose target references {metric!r}"
            )
            for panel in matching:
                description = panel.get("description", "")
                # The panel description SHOULD enumerate the outcomes
                # (operators rely on it to interpret the legend). All
                # tokens it lists must come from the allowlist; no
                # stale token can survive.
                found = _outcomes_in_description(description, allowlist)
                assert found, (
                    f"panel for {metric!r} (id={panel.get('id')}) lists no "
                    f"outcomes; description should enumerate at least one of "
                    f"{sorted(allowlist)}"
                )
                # And every outcome the description mentions must be
                # in the live allowlist (this is the load-bearing
                # assertion: it catches a drift where the allowlist
                # drops a token but the description keeps it).
                stale = found - allowlist
                assert not stale, (
                    f"panel for {metric!r} mentions outcomes {sorted(stale)} "
                    f"that are not in the live allowlist {sorted(allowlist)}"
                )
