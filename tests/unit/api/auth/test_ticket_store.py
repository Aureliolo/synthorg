"""Tests for WsTicketStore."""

import math
import re

import pytest

from synthorg.api.auth.ticket_store import TicketLimitExceededError, WsTicketStore
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole
from tests._shared.fake_clock import FakeClock


def _make_user(
    *,
    user_id: str = "test-user-001",
    username: str = "testadmin",
    role: HumanRole = HumanRole.CEO,
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username=username,
        role=role,
        auth_method=AuthMethod.WS_TICKET,
    )


@pytest.mark.unit
class TestWsTicketStoreCreate:
    """Tests for ticket creation."""

    def test_create_returns_url_safe_string(self) -> None:
        store = WsTicketStore()
        user = _make_user()
        ticket = store.create(user)

        assert isinstance(ticket, str)
        assert len(ticket) > 0
        # URL-safe base64 characters only
        assert re.fullmatch(r"[A-Za-z0-9_-]+", ticket)

    def test_create_returns_unique_tickets(self) -> None:
        store = WsTicketStore()
        # Use different user IDs to avoid per-user ticket cap
        tickets = {store.create(_make_user(user_id=f"user-{i}")) for i in range(100)}
        assert len(tickets) == 100

    def test_ttl_seconds_property(self) -> None:
        store = WsTicketStore(ttl_seconds=60.0)
        assert store.ttl_seconds == 60.0

    @pytest.mark.parametrize(
        ("ttl", "match"),
        [
            (0.0, "positive"),
            (-5.0, "positive"),
            (math.nan, "finite positive"),
            (math.inf, "finite positive"),
            (-math.inf, "finite positive"),
        ],
        ids=["zero", "negative", "nan", "inf", "neg_inf"],
    )
    def test_invalid_ttl_rejected(self, ttl: float, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            WsTicketStore(ttl_seconds=ttl)

    def test_per_user_ticket_cap(self) -> None:
        """Creating more than _MAX_PENDING_PER_USER tickets raises."""
        store = WsTicketStore()
        user = _make_user()
        for _ in range(5):
            store.create(user)
        with pytest.raises(TicketLimitExceededError):
            store.create(user)

    def test_per_user_ticket_cap_different_users(self) -> None:
        """Different users have independent ticket caps."""
        store = WsTicketStore()
        user_a = _make_user(user_id="user-a")
        user_b = _make_user(user_id="user-b")
        for _ in range(5):
            store.create(user_a)
        # user_b should still be able to create tickets
        ticket = store.create(user_b)
        assert isinstance(ticket, str)


@pytest.mark.unit
class TestWsTicketStoreValidateAndConsume:
    """Tests for ticket validation and consumption."""

    def test_validate_and_consume_returns_user(self) -> None:
        store = WsTicketStore()
        user = _make_user()
        ticket = store.create(user)

        result = store.validate_and_consume(ticket)

        assert result is not None
        assert result.user_id == user.user_id
        assert result.username == user.username
        assert result.role == user.role
        assert result.auth_method == AuthMethod.WS_TICKET

    def test_validate_and_consume_single_use(self) -> None:
        store = WsTicketStore()
        user = _make_user()
        ticket = store.create(user)

        first = store.validate_and_consume(ticket)
        second = store.validate_and_consume(ticket)

        assert first is not None
        assert second is None

    def test_validate_and_consume_single_use_concurrent(self) -> None:
        """Exactly one concurrent consumer wins the ticket."""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        store = WsTicketStore()
        user = _make_user()
        ticket = store.create(user)

        barrier = threading.Barrier(10)

        def consume() -> AuthenticatedUser | None:
            barrier.wait()
            return store.validate_and_consume(ticket)

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: consume(), range(10)))

        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        assert winners[0].user_id == user.user_id

    @pytest.mark.parametrize(
        ("ttl", "advance_by", "expect_consumable"),
        [
            pytest.param(10.0, 9.9, True, id="just_before_expiry"),
            # Exact boundary: ``validate_and_consume`` uses
            # ``now > entry.expires_at`` so ``now == expires_at`` is
            # still consumable. This case locks the contract down so
            # a future change of ``>`` to ``>=`` fails loudly.
            pytest.param(10.0, 10.0, True, id="exact_expiry_consumable"),
            pytest.param(10.0, 11.0, False, id="just_after_expiry"),
            pytest.param(5.0, 4.0, True, id="custom_ttl_within"),
            pytest.param(5.0, 5.0, True, id="custom_ttl_exact"),
            pytest.param(5.0, 6.0, False, id="custom_ttl_past"),
        ],
    )
    def test_validate_and_consume_ttl_boundary(
        self,
        ttl: float,
        advance_by: float,
        *,
        expect_consumable: bool,
    ) -> None:
        clock = FakeClock()
        store = WsTicketStore(ttl_seconds=ttl, clock=clock)
        user = _make_user()
        ticket = store.create(user)
        clock.advance(advance_by)
        result = store.validate_and_consume(ticket)
        if expect_consumable:
            assert result is not None
        else:
            assert result is None

    def test_validate_and_consume_unknown_ticket(self) -> None:
        store = WsTicketStore()
        result = store.validate_and_consume("nonexistent-ticket")
        assert result is None

    def test_validate_and_consume_empty_string(self) -> None:
        store = WsTicketStore()
        result = store.validate_and_consume("")
        assert result is None


@pytest.mark.unit
class TestWsTicketStoreCleanup:
    """Tests for expired ticket cleanup."""

    def test_cleanup_expired_removes_old_entries(self) -> None:
        clock = FakeClock()
        store = WsTicketStore(ttl_seconds=10.0, clock=clock)
        user = _make_user()
        store.create(user)
        store.create(user)
        clock.advance(11.0)
        assert store.cleanup_expired() == 2

    def test_cleanup_preserves_valid_entries(self) -> None:
        clock = FakeClock()
        store = WsTicketStore(ttl_seconds=10.0, clock=clock)
        user = _make_user()
        ticket = store.create(user)
        clock.advance(5.0)
        assert store.cleanup_expired() == 0
        assert store.validate_and_consume(ticket) is not None

    def test_cleanup_mixed_expired_and_valid(self) -> None:
        clock = FakeClock()
        store = WsTicketStore(ttl_seconds=10.0, clock=clock)
        user = _make_user()
        store.create(user)  # expires at +10
        clock.advance(8.0)
        valid_ticket = store.create(user)  # expires at +18
        clock.advance(4.0)  # now at +12: first expired, second valid
        assert store.cleanup_expired() == 1
        assert store.validate_and_consume(valid_ticket) is not None

    def test_cleanup_empty_store(self) -> None:
        store = WsTicketStore()
        removed = store.cleanup_expired()
        assert removed == 0
