"""Tests for SettingsSubscriber protocol."""

from collections.abc import Sequence

import pytest

from synthorg.settings.subscriber import SettingsSubscriber, describe_changes


class _ConformingSubscriber:
    """Minimal class that satisfies the SettingsSubscriber protocol."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset({("ns", "key")})

    @property
    def subscriber_name(self) -> str:
        return "test-subscriber"

    async def on_settings_changed(
        self,
        changes: Sequence[tuple[str, str]],
    ) -> None:
        self.seen.extend(changes)


class _MissingMethod:
    """Missing on_settings_changed method."""

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset()

    @property
    def subscriber_name(self) -> str:
        return "broken"


@pytest.mark.unit
class TestSettingsSubscriberProtocol:
    """SettingsSubscriber protocol conformance."""

    def test_runtime_checkable(self) -> None:
        """Protocol is runtime-checkable via isinstance."""
        sub = _ConformingSubscriber()
        assert isinstance(sub, SettingsSubscriber)

    def test_non_conforming_fails_isinstance(self) -> None:
        """Class missing on_settings_changed is not a subscriber."""
        broken = _MissingMethod()
        assert not isinstance(broken, SettingsSubscriber)

    def test_watched_keys_returns_frozenset(self) -> None:
        """watched_keys returns a frozenset of (namespace, key) tuples."""
        sub = _ConformingSubscriber()
        keys = sub.watched_keys
        assert isinstance(keys, frozenset)
        assert ("ns", "key") in keys

    def test_subscriber_name(self) -> None:
        """subscriber_name returns a string."""
        sub = _ConformingSubscriber()
        assert sub.subscriber_name == "test-subscriber"

    async def test_on_settings_changed_takes_a_batch(self) -> None:
        """on_settings_changed is awaitable and receives the whole batch."""
        sub = _ConformingSubscriber()

        await sub.on_settings_changed([("ns", "key"), ("ns", "other")])

        assert sub.seen == [("ns", "key"), ("ns", "other")]


@pytest.mark.unit
class TestDescribeChanges:
    """The shared log label every subscriber renders its batch with."""

    def test_a_short_batch_names_every_key(self) -> None:
        assert describe_changes([("ns", "a"), ("ns", "b")]) == "ns.a,ns.b"

    def test_a_long_batch_counts_the_remainder(self) -> None:
        # A form save carries every field an operator touched, so the label
        # stays readable without under-reporting how large the batch was.
        changes = [("ns", key) for key in ("a", "b", "c", "d", "e")]

        assert describe_changes(changes) == "ns.a,ns.b,ns.c+2"

    def test_publication_order_is_preserved(self) -> None:
        # Sorting would make the label stop matching the order the writes
        # landed in, which is what an operator reads it against.
        assert describe_changes([("ns", "z"), ("ns", "a")]) == "ns.z,ns.a"
