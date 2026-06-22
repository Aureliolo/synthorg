"""Unit tests for the shared subject-key / client-IP helpers.

These helpers decide which subject identifier (user vs IP vs fallback)
is used to bucket requests for both per-op rate-limit tiers.  A bug
here lets a malicious caller dodge the guard by spoofing
``X-Forwarded-For`` or by sending an unauthenticated request under a
key policy that expected a user.  The tests cover every branch of
``client_ip`` (scope override, untrusted peer, trusted peer with valid
XFF, trusted peer with only trusted hops in XFF, bogus XFF entry) and
``extract_subject_key`` (user / ip / user_or_ip policies with and
without an authenticated user).
"""

import ipaddress
from typing import Any
from unittest.mock import MagicMock

import pytest

from synthorg.api.rate_limits._subject import (
    SCOPE_KEY_TRUSTED_IP,
    STATE_KEY_TRUSTED_NETWORKS,
    STATE_KEY_TRUSTED_PROXIES,
    client_ip,
    extract_subject_key,
    parse_trusted_networks,
)
from tests._shared import AsgiDict

pytestmark = pytest.mark.unit


def _connection(  # type: ignore[explicit-any]  # MagicMock duck-types ASGIConnection  # noqa: PLR0913 -- test builder with many optional knobs
    *,
    peer: str | None = "203.0.113.5",
    headers: dict[str, str] | None = None,
    user_id: str | None = None,
    scope_override: str | None = None,
    trusted_proxies: frozenset[str] = frozenset(),
    cached_networks: tuple[
        ipaddress.IPv4Network | ipaddress.IPv6Network,
        ...,
    ]
    | None = None,
) -> Any:
    """Build a mock ``ASGIConnection`` with the shape the helpers need.

    The real ``ASGIConnection`` walks through a Litestar Request; the
    tests only touch ``scope``, ``headers``, and ``app.state``, so a
    minimal MagicMock with those three attributes matches the helpers'
    expectations without pulling in the full Litestar stack.
    """
    user = None
    if user_id is not None:
        user = MagicMock()
        user.user_id = user_id

    scope: AsgiDict = {
        "user": user,
        "client": (peer, 0) if peer is not None else None,
    }
    if scope_override is not None:
        scope[SCOPE_KEY_TRUSTED_IP] = scope_override

    state = MagicMock()
    setattr(state, STATE_KEY_TRUSTED_PROXIES, trusted_proxies)
    if cached_networks is not None:
        setattr(state, STATE_KEY_TRUSTED_NETWORKS, cached_networks)
    elif hasattr(state, STATE_KEY_TRUSTED_NETWORKS):
        # Default: no cached parsed tuple, helpers fall through to
        # on-the-fly parsing -- exercises the test-fixture path.
        delattr(state, STATE_KEY_TRUSTED_NETWORKS)

    app = MagicMock()
    app.state = state

    connection = MagicMock()
    connection.scope = scope
    connection.headers = headers or {}
    connection.app = app
    return connection


class TestParseTrustedNetworks:
    """``parse_trusted_networks`` parses well-formed entries, skips bad ones."""

    def test_empty_input_returns_empty_tuple(self) -> None:
        assert parse_trusted_networks(frozenset()) == ()

    def test_bare_ip_becomes_host_network(self) -> None:
        networks = parse_trusted_networks(frozenset({"10.0.0.5"}))
        assert len(networks) == 1
        assert ipaddress.ip_address("10.0.0.5") in networks[0]

    def test_cidr_range_covers_all_hosts(self) -> None:
        networks = parse_trusted_networks(frozenset({"10.0.0.0/8"}))
        assert ipaddress.ip_address("10.255.255.255") in networks[0]
        assert ipaddress.ip_address("11.0.0.1") not in networks[0]

    def test_ipv6_cidr_parses(self) -> None:
        networks = parse_trusted_networks(frozenset({"2001:db8::/32"}))
        assert len(networks) == 1
        assert ipaddress.ip_address("2001:db8::1") in networks[0]

    def test_malformed_entries_are_skipped_not_fatal(self) -> None:
        # One valid + one garbage -> the garbage is silently skipped so
        # a typo in ops config never disables the whole allowlist.
        networks = parse_trusted_networks(frozenset({"10.0.0.1", "not-an-ip"}))
        assert len(networks) == 1


class TestClientIp:
    """Branches of ``client_ip`` -- each proxy scenario covered."""

    def test_scope_override_wins(self) -> None:
        conn = _connection(
            peer="203.0.113.5",
            scope_override="198.51.100.9",
            trusted_proxies=frozenset({"203.0.113.0/24"}),
        )
        assert client_ip(conn) == "198.51.100.9"

    def test_untrusted_peer_xff_is_ignored(self) -> None:
        # XFF from an untrusted peer is attacker-controlled -- the
        # helper must return the raw peer, never parse the header.
        conn = _connection(
            peer="198.51.100.9",
            headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
            trusted_proxies=frozenset({"10.0.0.0/8"}),
        )
        assert client_ip(conn) == "198.51.100.9"

    def test_trusted_peer_walks_xff_right_to_left(self) -> None:
        # When the immediate peer is a trusted proxy the helper walks
        # XFF right-to-left and returns the first hop outside the
        # trusted set.  Rightmost hop is untrusted client -> returned.
        conn = _connection(
            peer="10.0.0.1",
            headers={"x-forwarded-for": "203.0.113.9, 10.0.0.2"},
            trusted_proxies=frozenset({"10.0.0.0/8"}),
        )
        assert client_ip(conn) == "203.0.113.9"

    def test_trusted_peer_all_hops_trusted_falls_back_to_peer(self) -> None:
        conn = _connection(
            peer="10.0.0.1",
            headers={"x-forwarded-for": "10.0.0.2, 10.0.0.3"},
            trusted_proxies=frozenset({"10.0.0.0/8"}),
        )
        assert client_ip(conn) == "10.0.0.1"

    def test_trusted_peer_empty_xff_falls_back_to_peer(self) -> None:
        conn = _connection(
            peer="10.0.0.1",
            headers={},
            trusted_proxies=frozenset({"10.0.0.0/8"}),
        )
        assert client_ip(conn) == "10.0.0.1"

    def test_trusted_peer_malformed_xff_entry_skipped(self) -> None:
        # Bad entries in XFF are ignored -- otherwise a caller behind
        # a trusted proxy could rotate garbage strings to evade both
        # rate-limit tiers by sitting in distinct buckets for each.
        # After skipping the garbage, the helper falls back to the
        # next untrusted hop (none here, so the peer).
        conn = _connection(
            peer="10.0.0.1",
            headers={"x-forwarded-for": "not-an-ip, 10.0.0.2"},
            trusted_proxies=frozenset({"10.0.0.0/8"}),
        )
        assert client_ip(conn) == "10.0.0.1"

    def test_trusted_peer_valid_hop_after_garbage_is_returned(self) -> None:
        # Garbage is skipped, but a valid untrusted IP further left
        # in the chain is still accepted as the real client.
        conn = _connection(
            peer="10.0.0.1",
            headers={"x-forwarded-for": "203.0.113.9, not-an-ip, 10.0.0.2"},
            trusted_proxies=frozenset({"10.0.0.0/8"}),
        )
        assert client_ip(conn) == "203.0.113.9"

    def test_no_peer_metadata_returns_unknown(self) -> None:
        conn = _connection(peer=None)
        assert client_ip(conn) == "unknown"

    def test_cached_networks_tuple_is_preferred(self) -> None:
        # When app.state carries the pre-parsed tuple, the helper uses
        # it directly and skips re-parsing the networks on every request.
        cached = parse_trusted_networks(frozenset({"10.0.0.0/8"}))
        conn = _connection(
            peer="10.0.0.1",
            headers={"x-forwarded-for": "203.0.113.9"},
            trusted_proxies=frozenset(),  # raw set intentionally empty
            cached_networks=cached,
        )
        assert client_ip(conn) == "203.0.113.9"


class TestExtractSubjectKey:
    """``user`` / ``ip`` / ``user_or_ip`` policies resolve correctly."""

    def test_user_policy_with_authenticated_user(self) -> None:
        conn = _connection(user_id="alice")
        assert (
            extract_subject_key(
                conn,
                "user",
                guard_name="per_op_rate_limit",
            )
            == "user:alice"
        )

    def test_user_policy_falls_back_to_ip_without_user(self) -> None:
        # Key design: unauthenticated requests still get throttled
        # instead of bypassing the limiter entirely.
        conn = _connection(peer="203.0.113.5", user_id=None)
        assert (
            extract_subject_key(
                conn,
                "user",
                guard_name="per_op_rate_limit",
            )
            == "ip:203.0.113.5"
        )

    def test_ip_policy_always_uses_ip_even_with_user(self) -> None:
        # External-facing endpoints (webhooks) bucket by IP regardless
        # of whether auth populated scope["user"].
        conn = _connection(user_id="alice", peer="203.0.113.5")
        assert (
            extract_subject_key(
                conn,
                "ip",
                guard_name="webhooks.receive",
            )
            == "ip:203.0.113.5"
        )

    def test_user_or_ip_prefers_user(self) -> None:
        conn = _connection(user_id="alice", peer="203.0.113.5")
        assert (
            extract_subject_key(
                conn,
                "user_or_ip",
                guard_name="per_op_rate_limit",
            )
            == "user:alice"
        )

    def test_user_or_ip_falls_back_to_ip_without_user(self) -> None:
        conn = _connection(user_id=None, peer="203.0.113.5")
        assert (
            extract_subject_key(
                conn,
                "user_or_ip",
                guard_name="per_op_rate_limit",
            )
            == "ip:203.0.113.5"
        )

    def test_user_namespace_cannot_collide_with_ip(self) -> None:
        # A literal user_id that looks like an IP must still be
        # prefixed with ``user:`` so the two namespaces never share a
        # bucket (otherwise a pathological user id could be crafted to
        # share a limiter bucket with a real IP).
        conn = _connection(user_id="203.0.113.5")
        assert (
            extract_subject_key(
                conn,
                "user",
                guard_name="per_op_rate_limit",
            )
            == "user:203.0.113.5"
        )
