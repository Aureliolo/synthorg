"""What the approval-raising step does when its collaborators fail.

Both halves of this module reach something that can be down: the settings read
that decides which offered pair is starred, and the compensating delete that
takes back an approval whose request never persisted. Each has a deliberate
non-raising answer, and a non-raising answer is only load-bearing while
something asserts it, because the failure it absorbs is invisible otherwise.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.types import NotBlankStr
from synthorg.hr.hiring_approval_submission import (
    propose_models,
    retire_unbacked_approval,
    submit_approval_item,
)
from synthorg.hr.models import HiringRequest
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of
from tests._shared.model_binding import provider_catalogue
from tests.unit.hr.conftest import make_candidate_card, make_hiring_request

pytestmark = pytest.mark.unit


class TestSpendProfileRead:
    """The profile decides which pair is recommended and nothing else."""

    async def test_an_unreadable_profile_still_proposes_the_pairs(self) -> None:
        """A settings read reaches a database, so it can time out.

        Aborting the hire over it would cost the operator every option on the
        strength of the field that only decides which one is starred.
        """
        resolver = mock_of[ConfigResolverProtocol](
            get_str=AsyncMock(
                spec=ConfigResolverProtocol.get_str,
                side_effect=TimeoutError("settings backend unreachable"),
            ),
        )

        proposal = await propose_models(
            make_candidate_card(),
            catalogue=provider_catalogue(),
            resolver=resolver,
        )

        assert proposal.recommended is not None

    async def test_no_resolver_at_all_is_the_same_answer(self) -> None:
        proposal = await propose_models(
            make_candidate_card(),
            catalogue=provider_catalogue(),
            resolver=None,
        )

        assert proposal.recommended is not None


class TestRetireUnbackedApproval:
    """The item lands before the request that explains it is written."""

    async def _submitted(self, store: ApprovalStoreProtocol) -> HiringRequest:
        """Raise an approval the way the service does.

        Returns:
            The approval-stamped request, as the caller that is about to fail
            to persist it holds it.
        """
        candidate = make_candidate_card()
        request = make_hiring_request(role="developer", candidates=(candidate,))
        proposal = await propose_models(
            candidate, catalogue=provider_catalogue(), resolver=None
        )
        return await submit_approval_item(
            store,
            request,
            candidate,
            candidate_id=str(candidate.id),
            proposal=proposal,
        )

    async def test_the_item_is_taken_back(self) -> None:
        store = ApprovalStore()
        stamped = await self._submitted(store)
        assert len(await store.list_items()) == 1

        await retire_unbacked_approval(store, request=stamped)

        assert await store.list_items() == ()

    async def test_a_failing_delete_does_not_replace_the_original_failure(
        self,
    ) -> None:
        """The caller is already unwinding the write that actually broke.

        Raising here reports the compensation instead of the cause, and the
        cause is the only one of the two an operator can act on.
        """
        stamped = await self._submitted(ApprovalStore())
        failing = mock_of[ApprovalStoreProtocol]()
        failing.delete.side_effect = ConnectionError("approval store unreachable")

        await retire_unbacked_approval(failing, request=stamped)

        assert failing.delete.await_args.args == (NotBlankStr(stamped.approval_id),)

    async def test_nothing_to_take_back_is_not_a_failure(self) -> None:
        """Both absences are ordinary: the refusal path writes neither."""
        request = make_hiring_request(role="developer")

        await retire_unbacked_approval(None, request=request)
        await retire_unbacked_approval(ApprovalStore(), request=request)
