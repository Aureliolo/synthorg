"""Opening a hire is all-or-nothing, because a half-open one has no exit.

Opening is create-then-submit, and the second half can be refused: no
configured model can run the role, the durable store rejects the write, no
candidate could be built. A live run left the first half behind, and because a
PENDING request counts as in flight, every later pass read it as "a hire is
already under way for this role" and staffed nothing, for ever.

The complement matters just as much: refusing must not become the new silence.
Once a model is configurable the next pass has to open a real hire, which it
can only do if nothing it abandoned is still standing in its way.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.engine.review_staffing.hiring_pass import ensure_hire_open
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.registry import AgentRegistryService
from tests._shared.model_binding import (
    TEST_MODEL_ID,
    MutableProviderCatalogue,
    provider_catalogue,
)

pytestmark = pytest.mark.unit

_ACTOR = "review-staffing-reconciler"


def _service(store: ApprovalStore, *, with_catalogue: bool) -> HiringService:
    """Build a hiring pipeline with or without anything to bind a hire to.

    Returns:
        The service under test.
    """
    return HiringService(
        registry=AgentRegistryService(),
        approval_store=store,
        provider_catalogue=provider_catalogue() if with_catalogue else None,
    )


class TestARefusedOpenLeavesNothingBehind:
    async def test_nothing_proposable_opens_no_hire(
        self,
    ) -> None:
        store = ApprovalStore()
        hiring = _service(store, with_catalogue=False)

        opened = await ensure_hire_open(
            hiring,
            COMPLETION_REVIEWER_ROLE_NAME,
            notifications=None,
            actor=_ACTOR,
        )

        assert opened is False
        assert await store.list_items() == ()

    async def test_the_role_is_not_left_looking_staffed(
        self,
    ) -> None:
        """A PENDING leftover reads as a hire under way and blocks every pass."""
        store = ApprovalStore()
        hiring = _service(store, with_catalogue=False)

        await ensure_hire_open(
            hiring,
            COMPLETION_REVIEWER_ROLE_NAME,
            notifications=None,
            actor=_ACTOR,
        )

        assert (
            hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
            is None
        )

    async def test_a_later_pass_can_still_open_a_real_hire(
        self,
    ) -> None:
        """The condition is transient: the operator configures a model.

        One service throughout, because the leftover this guards against lives
        in that service's own in-flight set. A second service would start with
        an empty one and pass whether or not the first cleaned up after itself.
        """
        store = ApprovalStore()
        catalogue = MutableProviderCatalogue()
        catalogue.delete_connection()
        hiring = HiringService(
            registry=AgentRegistryService(),
            approval_store=store,
            provider_catalogue=catalogue,
        )
        await ensure_hire_open(
            hiring, COMPLETION_REVIEWER_ROLE_NAME, notifications=None, actor=_ACTOR
        )

        catalogue.serve([TEST_MODEL_ID])
        opened = await ensure_hire_open(
            hiring,
            COMPLETION_REVIEWER_ROLE_NAME,
            notifications=None,
            actor=_ACTOR,
        )

        assert opened is True
        assert len(await store.list_items()) == 1

    async def test_repeated_refusals_do_not_accumulate_requests(
        self,
    ) -> None:
        """The sweep is level-triggered, so it re-asks on every cadence."""
        store = ApprovalStore()
        hiring = _service(store, with_catalogue=False)

        for _ in range(3):
            await ensure_hire_open(
                hiring,
                COMPLETION_REVIEWER_ROLE_NAME,
                notifications=None,
                actor=_ACTOR,
            )

        assert hiring.find_approved_requests() == ()
        assert await store.list_items() == ()


class TestAnOpenedHireIsNotDiscarded:
    async def test_the_open_request_survives_a_later_pass(
        self,
    ) -> None:
        """Exactly one open request per role, and it stays open for the operator."""
        store = ApprovalStore()
        hiring = _service(store, with_catalogue=True)

        first = await ensure_hire_open(
            hiring, COMPLETION_REVIEWER_ROLE_NAME, notifications=None, actor=_ACTOR
        )
        second = await ensure_hire_open(
            hiring, COMPLETION_REVIEWER_ROLE_NAME, notifications=None, actor=_ACTOR
        )

        assert first is True
        assert second is False
        assert len(await store.list_items()) == 1

    async def test_a_submitted_request_cannot_be_discarded(
        self,
    ) -> None:
        """The guard is the approval id: a card an operator can see stays."""
        store = ApprovalStore()
        hiring = _service(store, with_catalogue=True)
        await ensure_hire_open(
            hiring, COMPLETION_REVIEWER_ROLE_NAME, notifications=None, actor=_ACTOR
        )
        opened = hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
        assert opened is not None

        discarded = await hiring.discard_undecided_request(
            str(opened.id), reason="should not happen"
        )

        assert discarded is False
        assert (
            hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
            is not None
        )
