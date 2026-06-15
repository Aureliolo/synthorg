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

import pytest

from synthorg.observability.prometheus_labels import (
    VALID_APPROVAL_OUTCOMES,
    VALID_BLUEPRINT_OUTCOMES,
    VALID_ESCALATION_OUTCOMES,
)
from tests._shared import JsonDict

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


def _iter_panels(dashboard: JsonDict) -> list[JsonDict]:
    """Recursively yield every panel (including nested rows)."""
    panels: list[JsonDict] = []

    def _walk(node: JsonDict) -> None:
        for panel in node.get("panels", []) or []:
            panels.append(panel)
            if "panels" in panel:
                _walk(panel)

    _walk(dashboard)
    return panels


def _panel_metric(panel: JsonDict) -> str | None:
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


class TestGrafanaDashboardCorrectness:
    """Pins the dashboard correctness and metric-visibility invariants."""

    @staticmethod
    def _panels() -> list[JsonDict]:
        dashboard = json.loads(_DASHBOARD_PATH.read_text(encoding="utf-8"))
        return _iter_panels(dashboard)

    @staticmethod
    def _all_exprs(panels: list[JsonDict]) -> str:
        return " ".join(
            target.get("expr") or ""
            for panel in panels
            for target in panel.get("targets", []) or []
        )

    def test_app_info_metric_used_not_bare(self) -> None:
        """No panel queries the bare ``synthorg_app`` series.

        ``prometheus_client`` appends ``_info`` to Info metrics, so the
        real series is ``synthorg_app_info``; a bare ``synthorg_app``
        target is a permanent 'No data'.
        """
        for panel in self._panels():
            for target in panel.get("targets", []) or []:
                expr = target.get("expr") or ""
                assert not re.search(r"\bsynthorg_app\b", expr), (
                    f"panel id={panel.get('id')} queries bare synthorg_app; "
                    "use synthorg_app_info"
                )
        assert "synthorg_app_info" in self._all_exprs(self._panels())

    def test_audit_append_panels_are_differentiated(self) -> None:
        """Panels 601 and 821 must not run the identical query."""
        panels = {p.get("id"): p for p in self._panels()}
        assert 601 in panels and 821 in panels  # noqa: PT018
        expr_601 = panels[601]["targets"][0]["expr"]
        expr_821 = panels[821]["targets"][0]["expr"]
        assert expr_601 != expr_821, (
            "panels 601 and 821 run the identical audit-append query; "
            "differentiate or remove one"
        )

    def test_previously_invisible_metric_families_are_charted(self) -> None:
        """The emitted-but-invisible families now have at least one panel."""
        exprs = self._all_exprs(self._panels())
        for metric in (
            "synthorg_ws_connection_lifetime_seconds",
            "synthorg_ws_revalidation_total",
            "synthorg_ws_active_connections",
            "synthorg_pg_pool_size",
            "synthorg_pg_pool_active_connections",
            "synthorg_pg_pool_acquire_duration_seconds",
            "synthorg_pg_pool_exhausted_total",
            "synthorg_push_queue_events_total",
            "synthorg_log_sink_events_total",
        ):
            assert metric in exprs, f"{metric} has no Grafana panel"
