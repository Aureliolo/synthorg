"""Unit tests for the OpenHands loop boot-wiring helpers.

Covers the pure derivations (egress allowlist, complexity-rule merge) that
decide the loop's sandbox egress pin and its per-complexity selectability;
the full ``app_state`` wiring paths are exercised in the integration tier.
"""

import pytest

from synthorg.core.task_enums import Complexity
from synthorg.workers._openhands_wiring import (
    _egress_allowlist,
    _host_port,
    _merge_complexity_rules,
    _missing_pieces,
)

pytestmark = pytest.mark.unit


def _pieces(
    *,
    enabled: bool = True,
    signer: object | None = None,
    gateway: str = "http://gw",
    mcp: str = "http://mcp",
) -> tuple[str, ...]:
    """Call ``_missing_pieces`` with the wired-and-enabled case as the baseline.

    Returns:
        The names of the missing pieces for this combination.
    """
    return _missing_pieces(
        enabled=enabled,
        signer=object() if signer is None else signer,
        gateway_host=gateway,
        mcp_host=mcp,
    )


def test_missing_pieces_empty_when_all_wired() -> None:
    assert _pieces() == ()


def test_missing_pieces_names_absent_signer() -> None:
    assert _missing_pieces(
        enabled=True, signer=None, gateway_host="http://gw", mcp_host="http://mcp"
    ) == ("gateway_signer",)


def test_missing_pieces_names_blank_endpoints() -> None:
    assert _pieces(gateway="") == ("providers.gateway_base_url",)
    assert _pieces(mcp="") == ("tools.credentialed_mcp_base_url",)


def test_missing_pieces_reports_every_absent_piece() -> None:
    # A cold boot with nothing wired names all three so the log is actionable.
    assert _missing_pieces(enabled=True, signer=None, gateway_host="", mcp_host="") == (
        "gateway_signer",
        "providers.gateway_base_url",
        "tools.credentialed_mcp_base_url",
    )


def test_missing_pieces_names_only_the_master_when_disabled() -> None:
    # An operator who turned the capability off gets that named as the single
    # cause, not a list of endpoints they never asked to wire.
    assert _pieces(enabled=False, gateway="", mcp="") == ("tools.openhands_enabled",)


def test_host_port_infers_scheme_default_ports() -> None:
    assert _host_port("http://host.internal:8000/api/v1/gateway/v1") == (
        "host.internal:8000"
    )
    assert _host_port("http://plain.host/path") == "plain.host:80"
    assert _host_port("https://secure.host/path") == "secure.host:443"
    assert _host_port("not a url") == ""


def test_egress_allowlist_dedupes_same_host() -> None:
    # Gateway + MCP on the same host:port collapse to a single allow entry.
    allow = _egress_allowlist(
        "http://host:8000/api/v1/gateway/v1", "http://host:8000/api/v1/mcp-gateway"
    )
    assert allow == ("host:8000",)


def test_egress_allowlist_keeps_distinct_hosts_sorted() -> None:
    allow = _egress_allowlist(
        "http://gw.host:8000/gateway", "http://mcp.host:9000/mcp-gateway"
    )
    assert allow == ("gw.host:8000", "mcp.host:9000")


def test_merge_complexity_rules_defaults_when_empty() -> None:
    rules = _merge_complexity_rules("")
    by_complexity = {r.complexity: r.loop_type for r in rules}
    assert by_complexity[Complexity.SIMPLE] == "react"
    assert by_complexity[Complexity.MEDIUM] == "plan_execute"
    assert by_complexity[Complexity.COMPLEX] == "hybrid"
    assert by_complexity[Complexity.EPIC] == "hybrid"


def test_merge_complexity_rules_overrides_selected_complexities() -> None:
    rules = _merge_complexity_rules("complex:openhands,epic:openhands")
    by_complexity = {r.complexity: r.loop_type for r in rules}
    # Overridden complexities route to openhands; the rest keep defaults.
    assert by_complexity[Complexity.COMPLEX] == "openhands"
    assert by_complexity[Complexity.EPIC] == "openhands"
    assert by_complexity[Complexity.SIMPLE] == "react"
    assert by_complexity[Complexity.MEDIUM] == "plan_execute"
    # Every complexity appears exactly once (no duplicate rules).
    assert len(rules) == len({r.complexity for r in rules})
