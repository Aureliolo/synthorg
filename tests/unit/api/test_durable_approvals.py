"""Pending human decisions must survive a restart.

An approval held only in memory is lost to a restart, and a plan left at
`PENDING_REVIEW` then has nothing to approve and no route to re-create the
decision. Every durable piece exists (table, protocol, both backend
repositories, dual-backend conformance, and `ApprovalStore`'s own `repo`
support), so the thing worth pinning is that the production construction
site actually attaches one.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.approval_store_autowire import (
    wire_durable_approvals,
)
from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.persistence.approval_protocol import ApprovalRepository
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


class TestAttachRepo:
    """The seam that hands the store its durable half after connect."""

    def test_a_fresh_store_has_no_durable_repo(self) -> None:
        assert ApprovalStore().has_persistent_repo is False

    def test_attaching_makes_the_store_durable(self) -> None:
        store = ApprovalStore()

        store.attach_repo(mock_of[ApprovalRepository]())

        assert store.has_persistent_repo is True

    def test_none_never_unbinds_a_live_repo(self) -> None:
        """The only reason to pass None is having nothing to offer.

        Letting that unbind a working repository would turn a wiring gap
        into silent data loss, which is the failure this seam ends.
        """
        store = ApprovalStore()
        store.attach_repo(mock_of[ApprovalRepository]())

        store.attach_repo(None)

        assert store.has_persistent_repo is True

    def test_rebinding_a_second_repo_is_refused(self) -> None:
        """Two stores would split the queue between them."""
        first = mock_of[ApprovalRepository]()
        store = ApprovalStore()
        store.attach_repo(first)

        store.attach_repo(mock_of[ApprovalRepository]())

        assert store._repo is first


class TestStartupWiring:
    """The boot hook that runs once persistence is connected."""

    def test_an_unconnected_backend_leaves_the_store_in_memory(self) -> None:
        """Degrade, never raise: a boot that cannot offer durability boots."""
        store = ApprovalStore()
        app_state = _app_state_with(store)

        wire_durable_approvals(app_state, None)

        assert store.has_persistent_repo is False

    def test_a_substituted_store_is_left_alone(self) -> None:
        """An injected store owns its own persistence, as at construction."""
        substitute = mock_of[ApprovalStoreProtocol]()
        app_state = _app_state_with(substitute)

        wire_durable_approvals(app_state, None)

        assert substitute.method_calls == []


def _app_state_with(store: ApprovalStoreProtocol) -> AppState:
    """Build the smallest app-state stand-in the wiring reads.

    Args:
        store: The approval store the wiring should find on the state.

    Returns:
        An app state carrying *store*.
    """
    return make_app_state(approval_store=store)
