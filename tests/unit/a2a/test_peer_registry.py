"""Tests for the external peer discovery registry."""

import pytest

from synthorg.a2a.models import A2AAgentCard
from synthorg.a2a.peer_registry import PeerRegistry


def _make_card(name: str = "peer-agent") -> A2AAgentCard:
    """Create a minimal Agent Card for testing."""
    return A2AAgentCard(
        name=name,
        url="https://example.com/a2a",
    )


class TestPeerRegistry:
    """PeerRegistry CRUD operations."""

    @pytest.mark.unit
    async def test_register_and_get(self) -> None:
        """Register a peer and retrieve its card."""
        reg = PeerRegistry()
        card = _make_card()
        await reg.register("peer-a", card)

        result = await reg.get("peer-a")
        assert result is not None
        assert result.name == "peer-agent"

    @pytest.mark.unit
    async def test_get_missing(self) -> None:
        """Get returns None for unknown peers."""
        reg = PeerRegistry()
        assert await reg.get("missing") is None

    @pytest.mark.unit
    async def test_case_insensitive(self) -> None:
        """Peer names are case-insensitive."""
        reg = PeerRegistry()
        await reg.register("Peer-A", _make_card())
        assert await reg.get("peer-a") is not None
        assert await reg.get("PEER-A") is not None

    @pytest.mark.unit
    async def test_whitespace_insensitive(self) -> None:
        """Peer names are stripped before lookup (shared normaliser)."""
        reg = PeerRegistry()
        await reg.register("  Peer-A  ", _make_card())
        assert await reg.get("peer-a") is not None
        assert await reg.remove("\tPEER-A\n") is True

    @pytest.mark.unit
    async def test_remove(self) -> None:
        """Remove a registered peer."""
        reg = PeerRegistry()
        await reg.register("peer-a", _make_card())
        assert await reg.remove("peer-a") is True
        assert await reg.get("peer-a") is None

    @pytest.mark.unit
    async def test_remove_missing(self) -> None:
        """Remove returns False for unknown peers."""
        reg = PeerRegistry()
        assert await reg.remove("missing") is False

    @pytest.mark.unit
    async def test_list_peers(self) -> None:
        """List all registered peer names."""
        reg = PeerRegistry()
        await reg.register("peer-a", _make_card("a"))
        await reg.register("peer-b", _make_card("b"))

        peers = await reg.list_peers()
        assert set(peers) == {"peer-a", "peer-b"}

    @pytest.mark.unit
    async def test_list_empty(self) -> None:
        """Empty registry returns empty tuple."""
        reg = PeerRegistry()
        assert await reg.list_peers() == ()

    @pytest.mark.unit
    async def test_update_existing(self) -> None:
        """Re-registering updates the card."""
        reg = PeerRegistry()
        await reg.register("peer-a", _make_card("old"))
        await reg.register("peer-a", _make_card("new"))

        card = await reg.get("peer-a")
        assert card is not None
        assert card.name == "new"

    @pytest.mark.unit
    async def test_register_deep_copies_input(self) -> None:
        """Register stores a deep copy (input mutation is isolated)."""
        reg = PeerRegistry()
        card = _make_card()
        await reg.register("peer-a", card)

        # get() returns the stored object directly (no per-read copy).
        # If register() deep-copied, stored object identity differs.
        retrieved = await reg.get("peer-a")
        assert retrieved is not card

        # Also verify via the internal backing store.
        stored = reg._peers.get("peer-a")
        assert stored is not card
        assert stored is retrieved  # get() returns the stored ref

    @pytest.mark.unit
    async def test_backing_store_is_immutable(self) -> None:
        """Internal peers mapping is a MappingProxyType (read-only)."""
        from types import MappingProxyType

        reg = PeerRegistry()
        await reg.register("peer-a", _make_card())
        assert isinstance(reg._peers, MappingProxyType)

    @pytest.mark.unit
    async def test_cow_on_register(self) -> None:
        """Register creates a new mapping (copy-on-write)."""
        reg = PeerRegistry()
        await reg.register("peer-a", _make_card("a"))
        first_ref = reg._peers
        await reg.register("peer-b", _make_card("b"))
        second_ref = reg._peers
        assert first_ref is not second_ref

    @pytest.mark.unit
    async def test_cow_on_remove(self) -> None:
        """Remove creates a new mapping (copy-on-write)."""
        reg = PeerRegistry()
        await reg.register("peer-a", _make_card())
        before = reg._peers
        await reg.remove("peer-a")
        after = reg._peers
        assert before is not after
