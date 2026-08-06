"""Unit tests for the OpenHands loop boot-wiring helpers.

Covers the pure derivations (egress allowlist, path narrowing, complexity-rule
merge) that decide the loop's sandbox egress pin and its per-complexity
selectability. The gate itself, ``build_openhands_loop_deps_or_none``, is
driven end to end in ``test_openhands_deps_gate.py``.
"""

import pytest

from synthorg.core.task_enums import Complexity
from synthorg.engine.loop_selector import LoopType
from synthorg.workers._openhands_wiring import (
    _egress_allowlist,
    _egress_path_rules,
    _host_port,
    _merge_complexity_rules,
    _missing_pieces,
    _url_path,
)

pytestmark = pytest.mark.unit


_PRESENT = object()


def _pieces(
    *,
    enabled: bool = True,
    signer: object | None = _PRESENT,
    gateway: str = "http://gw",
    mcp: str = "http://mcp",
) -> tuple[str, ...]:
    """Call ``_missing_pieces`` with the wired-and-enabled case as the baseline.

    ``signer=None`` means what it means in the function under test, an absent
    signer, so every case can go through this helper.

    Returns:
        The names of the missing pieces for this combination.
    """
    return _missing_pieces(
        enabled=enabled,
        signer=signer,
        gateway_host=gateway,
        mcp_host=mcp,
    )


def test_missing_pieces_empty_when_all_wired() -> None:
    assert _pieces() == ()


def test_missing_pieces_names_absent_signer() -> None:
    assert _pieces(signer=None) == ("gateway_signer",)


@pytest.mark.parametrize(
    ("blank", "expected"),
    [
        ({"gateway": ""}, "providers.gateway_base_url"),
        ({"mcp": ""}, "tools.credentialed_mcp_base_url"),
    ],
)
def test_missing_pieces_names_blank_endpoints(
    blank: dict[str, str], expected: str
) -> None:
    assert _pieces(**blank) == (expected,)  # type: ignore[arg-type]


def test_missing_pieces_reports_every_absent_piece() -> None:
    # A cold boot with nothing wired names all three so the log is actionable.
    assert _pieces(signer=None, gateway="", mcp="") == (
        "gateway_signer",
        "providers.gateway_base_url",
        "tools.credentialed_mcp_base_url",
    )


def test_missing_pieces_names_only_the_master_when_disabled() -> None:
    # An operator who turned the capability off gets that named as the single
    # cause, not a list of endpoints they never asked to wire.
    assert _pieces(enabled=False, gateway="", mcp="") == ("tools.openhands_enabled",)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://host.internal:8000/api/v1/gateway/v1", "host.internal:8000"),
        ("http://plain.host/path", "plain.host:80"),
        ("https://secure.host/path", "secure.host:443"),
        ("not a url", ""),
    ],
    ids=["explicit_port", "http_default", "https_default", "unparseable"],
)
def test_host_port_infers_scheme_default_ports(url: str, expected: str) -> None:
    assert _host_port(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://host:8000/api/v1/gateway/v1", "/api/v1/gateway/v1"),
        ("http://host:8000/api/v1/mcp-gateway/", "/api/v1/mcp-gateway"),
        ("http://host:8000", ""),
        ("http://host:8000/", ""),
    ],
    ids=["gateway", "trailing_slash", "no_path", "root_only"],
)
def test_url_path_extracts_the_narrowing_prefix(url: str, expected: str) -> None:
    assert _url_path(url) == expected


def test_egress_path_rules_narrow_both_endpoints_on_the_shared_host() -> None:
    # Both endpoints share one backend process, so the host allowlist alone
    # would also grant every other route it serves.
    rules = _egress_path_rules(
        "http://host:8000/api/v1/gateway/v1", "http://host:8000/api/v1/mcp-gateway"
    )
    assert rules == (
        "host:8000=/api/v1/gateway/v1",
        "host:8000=/api/v1/mcp-gateway",
    )


def test_egress_path_rules_drop_an_endpoint_with_no_path() -> None:
    rules = _egress_path_rules(
        "http://host:8000", "http://host:8000/api/v1/mcp-gateway"
    )
    assert rules == ("host:8000=/api/v1/mcp-gateway",)


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
    assert set(by_complexity) == set(Complexity)
    assert set(by_complexity.values()) == {LoopType.REACT}


def test_merge_complexity_rules_overrides_selected_complexities() -> None:
    rules = _merge_complexity_rules("complex:openhands,epic:openhands")
    by_complexity = {r.complexity: r.loop_type for r in rules}
    # Overridden complexities route to openhands; the rest keep defaults.
    assert by_complexity[Complexity.COMPLEX] is LoopType.OPENHANDS
    assert by_complexity[Complexity.EPIC] is LoopType.OPENHANDS
    assert by_complexity[Complexity.SIMPLE] is LoopType.REACT
    assert by_complexity[Complexity.MEDIUM] is LoopType.REACT
    # Every complexity appears exactly once (no duplicate rules).
    assert len(rules) == len({r.complexity for r in rules})


def test_merge_complexity_rules_coerces_a_retired_loop_name() -> None:
    rules = _merge_complexity_rules("medium:hybrid,complex:plan_execute")
    by_complexity = {r.complexity: r.loop_type for r in rules}
    assert by_complexity[Complexity.MEDIUM] is LoopType.REACT
    assert by_complexity[Complexity.COMPLEX] is LoopType.REACT
