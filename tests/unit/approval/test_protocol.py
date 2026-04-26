"""Structural conformance tests for ``ApprovalStoreProtocol``.

Locks the runtime-checkable contract against the concrete
``ApprovalStore`` so a future method removal on the concrete fails CI
instead of silently breaking the abstraction for engine, security, and
hr callers.
"""

import inspect

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.protocol import (
    ApprovalStoreProtocol,
    SyncResettableApprovalStore,
)

pytestmark = pytest.mark.unit


class TestApprovalStoreProtocol:
    """``ApprovalStore`` must satisfy ``ApprovalStoreProtocol``."""

    def test_concrete_satisfies_protocol(self) -> None:
        """``isinstance(store, ApprovalStoreProtocol)`` is True.

        Proves the runtime structural check binds: every method the
        protocol declares exists on the concrete.
        """
        store = ApprovalStore()
        assert isinstance(store, ApprovalStoreProtocol)

    def test_clear_is_async_on_protocol_and_concrete(self) -> None:
        """``clear`` must be a coroutine function on both sides.

        The async signature is what carries the lock-holding contract;
        a sync override would silently bypass ``self._lock`` and
        reintroduce the partial-clear race the hardening fixed.
        """
        assert inspect.iscoroutinefunction(ApprovalStoreProtocol.clear)
        assert inspect.iscoroutinefunction(ApprovalStore.clear)

    def test_concrete_satisfies_test_reset_protocol(self) -> None:
        """``ApprovalStore`` also satisfies the test-only reset hatch."""
        store = ApprovalStore()
        assert isinstance(store, SyncResettableApprovalStore)

    def test_reset_for_test_sync_is_synchronous(self) -> None:
        """``reset_for_test_sync`` must NOT be a coroutine function.

        Sync pytest fixtures call it without an event loop; making it
        async would either hang the fixture or require everyone to
        rewrite to async, defeating the escape-hatch purpose.
        """
        assert not inspect.iscoroutinefunction(
            SyncResettableApprovalStore.reset_for_test_sync,
        )
        assert not inspect.iscoroutinefunction(ApprovalStore.reset_for_test_sync)

    def test_protocol_surface_is_stable(self) -> None:
        """The protocol's public method names are the agreed surface."""
        expected = {
            "add",
            "clear",
            "get",
            "list_items",
            "save",
            "save_if_pending",
        }
        actual = {
            name for name in vars(ApprovalStoreProtocol) if not name.startswith("_")
        }
        assert actual == expected, (
            "ApprovalStoreProtocol surface changed: "
            f"missing={expected - actual}, added={actual - expected}"
        )

    def test_test_reset_protocol_surface_is_stable(self) -> None:
        """The test-reset protocol exposes only the sync escape hatch."""
        expected = {"reset_for_test_sync"}
        actual = {
            name
            for name in vars(SyncResettableApprovalStore)
            if not name.startswith("_")
        }
        assert actual == expected
