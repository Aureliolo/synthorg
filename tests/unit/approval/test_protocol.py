"""Structural conformance tests for ``ApprovalStoreProtocol``.

Locks the runtime-checkable contract against the concrete
``ApprovalStore`` so a future method removal on the concrete fails CI
instead of silently breaking the abstraction for engine, security, and
hr callers.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.protocol import ApprovalStoreProtocol

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

    def test_protocol_surface_is_stable(self) -> None:
        """The protocol's public method names are the agreed surface."""
        expected = {
            "add",
            "clear",
            "reset_for_test_sync",
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
