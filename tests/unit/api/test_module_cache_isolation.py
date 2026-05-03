"""Regression coverage for global autouse resets of module-level caches.

Two subsystems hold a process-global cache by design (the Agent Card
cache and the Prometheus label-validator snapshot). The autouse fixtures
in ``tests/conftest.py`` clear them before every test so xdist workers
and ``--count N`` repeat runs cannot inherit stale entries from a prior
test in the same worker.

These tests live deliberately outside ``tests/unit/a2a/`` and
``tests/unit/observability/`` -- the original local fixtures only
covered the directory they were defined in, so this file is the
regression that catches a future refactor that scopes the resets back
down. If either fixture stops firing globally, one of these assertions
trips immediately.
"""

import pytest

pytestmark = pytest.mark.unit


class TestA2aCardCacheGlobalReset:
    """``synthorg.a2a.well_known._card_cache`` resets before every test."""

    async def test_cache_starts_empty_outside_a2a_directory(self) -> None:
        from synthorg.a2a.well_known import _card_cache

        assert _card_cache == {}

    async def test_pollution_does_not_leak_between_tests_a(self) -> None:
        # Stage 1: populate the cache. The autouse fixture in conftest
        # runs BEFORE this test, so the cache is empty here too; the
        # population is the test's own contribution that the next test
        # must not see.
        from synthorg.a2a.well_known import _card_cache

        _card_cache["host-a:test-key"] = ({"name": "test"}, 1.0e30, "fp")
        assert "host-a:test-key" in _card_cache

    async def test_pollution_does_not_leak_between_tests_b(self) -> None:
        # Stage 2: assert the cache is empty again. With the autouse
        # global reset, the populate-on-A entry was cleared before B
        # started. Without the reset, ordering randomisation
        # (pytest-randomly) could surface this as flake.
        from synthorg.a2a.well_known import _card_cache

        assert _card_cache == {}


class TestPrometheusLabelSnapshotGlobalReset:
    """``prometheus_labels._snapshot`` resets before every test."""

    async def test_snapshot_starts_empty_outside_observability_directory(
        self,
    ) -> None:
        from synthorg.observability.prometheus_labels import (
            _INITIAL_SNAPSHOT,
            _snapshot_for_collector,
        )

        # The reset rebinds the module global to ``_INITIAL_SNAPSHOT``;
        # asserting on identity proves the reset actually fired (rather
        # than just hitting a freshly-bootstrapped value).
        assert _snapshot_for_collector() is _INITIAL_SNAPSHOT

    async def test_pollution_does_not_leak_between_tests_a(self) -> None:
        # Stage 1: drive ``update_label_snapshot`` to seed non-empty
        # state. The autouse fixture must clear it before the next test.
        from synthorg.observability.prometheus_labels import (
            _LabelSnapshot,
            update_label_snapshot,
        )

        update_label_snapshot(
            _LabelSnapshot(
                agent_ids=frozenset({"agent-x"}),
                agent_ids_seeded=True,
            ),
        )

    async def test_pollution_does_not_leak_between_tests_b(self) -> None:
        # Stage 2: snapshot is bootstrap again because the autouse
        # fixture rebinds it. Without the reset, ordering randomisation
        # could surface this as flake.
        from synthorg.observability.prometheus_labels import (
            _INITIAL_SNAPSHOT,
            _snapshot_for_collector,
        )

        assert _snapshot_for_collector() is _INITIAL_SNAPSHOT
