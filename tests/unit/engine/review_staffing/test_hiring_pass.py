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
from synthorg.engine.review_staffing.hiring_pass import (
    ensure_hire_open,
    finish_approved_hires,
)
from synthorg.hr.enums import HiringRequestStatus
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


class TestAnUncompletableHireIsWithdrawn:
    """A pass that can never succeed must not run for ever pretending it can.

    A live deployment held one approved request whose pair had gone: the
    sweep re-failed on it every cadence for seven days, it appeared on no
    dashboard page, and no pass could reach an exit for it.
    """

    @staticmethod
    async def _approved_on_a_lost_pair() -> tuple[HiringService, str]:
        """Approve a hire, then take away the connection it names.

        Returns:
            The service and the approved request's id.
        """
        store = ApprovalStore()
        catalogue = MutableProviderCatalogue()
        hiring = HiringService(
            registry=AgentRegistryService(),
            approval_store=store,
            provider_catalogue=catalogue,
        )
        await ensure_hire_open(
            hiring, COMPLETION_REVIEWER_ROLE_NAME, notifications=None, actor=_ACTOR
        )
        opened = hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
        assert opened is not None
        await hiring.approve_request(str(opened.id), decided_by="operator")
        catalogue.delete_connection()
        return hiring, str(opened.id)

    async def test_a_pass_withdraws_it_rather_than_retrying_for_ever(
        self,
    ) -> None:
        hiring, request_id = await self._approved_on_a_lost_pair()

        completed = await finish_approved_hires(hiring, notifications=None)

        assert completed == 0
        assert hiring.find_approved_requests() == ()
        withdrawn = hiring.get_request(request_id)
        assert withdrawn is not None
        assert withdrawn.status is HiringRequestStatus.REJECTED

    async def test_a_later_pass_can_open_a_fresh_hire_for_the_role(self) -> None:
        # The withdrawal is only worth anything if it unblocks the role: a
        # request left in flight is what made every later pass staff nothing.
        hiring, _ = await self._approved_on_a_lost_pair()
        await finish_approved_hires(hiring, notifications=None)

        assert (
            hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
            is None
        )

    async def test_an_approved_request_with_no_candidate_is_withdrawn_too(
        self,
    ) -> None:
        # The second uncompletable shape, and the one a lost pair does not
        # cover: the request's own candidate card is not on it, so every pass
        # raises InvalidCandidateError on the same frozen data. Caught as an
        # ordinary DomainError it retried for ever, exactly as a lost pair did
        # before it had a class of its own.
        store = ApprovalStore()
        hiring = _service(store, with_catalogue=True)
        await ensure_hire_open(
            hiring, COMPLETION_REVIEWER_ROLE_NAME, notifications=None, actor=_ACTOR
        )
        opened = hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
        assert opened is not None
        request_id = str(opened.id)
        await hiring.approve_request(request_id, decided_by="operator")
        # Written directly because no public call produces it: approval
        # selects a candidate. A durable row can still carry the shape (one
        # persisted before its candidates were, or a partial write), and
        # rehydration puts it straight back into this sweep's query.
        approved = hiring.get_request(request_id)
        assert approved is not None
        hiring._requests[request_id] = approved.model_copy(
            update={"selected_candidate_id": None}
        )

        completed = await finish_approved_hires(hiring, notifications=None)

        assert completed == 0
        assert hiring.find_approved_requests() == ()
        withdrawn = hiring.get_request(request_id)
        assert withdrawn is not None
        assert withdrawn.status is HiringRequestStatus.REJECTED

    async def test_a_transient_block_is_still_retried(self) -> None:
        # The complement, so withdrawal cannot become the new silence: a
        # catalogue that comes back must still finish the hire the operator
        # approved, rather than finding it withdrawn.
        store = ApprovalStore()
        catalogue = MutableProviderCatalogue()
        hiring = HiringService(
            registry=AgentRegistryService(),
            approval_store=store,
            provider_catalogue=catalogue,
        )
        await ensure_hire_open(
            hiring, COMPLETION_REVIEWER_ROLE_NAME, notifications=None, actor=_ACTOR
        )
        opened = hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
        assert opened is not None
        await hiring.approve_request(str(opened.id), decided_by="operator")

        completed = await finish_approved_hires(hiring, notifications=None)

        assert completed == 1


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
