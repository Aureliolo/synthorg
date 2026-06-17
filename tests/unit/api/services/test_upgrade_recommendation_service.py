"""Tests for the upgrade-recommendation application service."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.api.services.upgrade_recommendation_service import (
    UpgradeRecommendationService,
)
from synthorg.organization.models import UpdateAgentOrgRequest
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationRepository,
)
from synthorg.providers.enums import RecommendationStatus
from synthorg.providers.errors import (
    UpgradeRecommendationAlreadyDecidedError,
    UpgradeRecommendationNotFoundError,
)
from synthorg.providers.management.upgrade_models import (
    StoredUpgradeRecommendation,
    UpgradeRecommendation,
)
from tests._shared import FakeClock, as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _stored(
    *, rec_id: str = "rec-1", agents: tuple[str, ...] = (sid("writer"),)
) -> StoredUpgradeRecommendation:
    return StoredUpgradeRecommendation(
        id=as_uuid(rec_id),
        recommendation=UpgradeRecommendation(
            provider_name="example-provider",
            current_model_id="old",
            recommended_model_id="new",
            family="fam",
            current_generation=1.0,
            recommended_generation=2.0,
            score=0.6,
            reason="x",
        ),
        agent_ids=agents,
        created_at=_NOW,
    )


def _service(
    *,
    repo: UpgradeRecommendationRepository,
    org_mutations: OrgMutationService,
) -> UpgradeRecommendationService:
    return UpgradeRecommendationService(
        repo=repo,
        org_mutations=org_mutations,
        clock=FakeClock(),
    )


class TestUpgradeRecommendationService:
    async def test_get_or_404_raises_when_absent(self) -> None:
        repo = mock_of[UpgradeRecommendationRepository](
            get=AsyncMock(return_value=None)
        )
        service = _service(repo=repo, org_mutations=mock_of[OrgMutationService]())
        with pytest.raises(UpgradeRecommendationNotFoundError):
            await service.get_or_404(as_uuid("missing"))

    async def test_approve_transitions_and_reassigns(self) -> None:
        stored = _stored()
        repo = mock_of[UpgradeRecommendationRepository](
            get=AsyncMock(return_value=stored),
            transition_if=AsyncMock(return_value=True),
        )
        org = mock_of[OrgMutationService](update_agent=AsyncMock())
        service = _service(repo=repo, org_mutations=org)
        await service.approve(stored.id, decided_by="op")
        repo.transition_if.assert_awaited_once()
        cas = repo.transition_if.await_args
        assert cas.args[0] == stored.id
        assert cas.kwargs["from_state"] is RecommendationStatus.PENDING
        assert cas.kwargs["to_state"] is RecommendationStatus.APPROVED
        assert cas.kwargs["decided_by"] == "op"
        org.update_agent.assert_awaited_once_with(
            sid("writer"),
            UpdateAgentOrgRequest(model_provider="example-provider", model_id="new"),
        )

    async def test_approve_already_decided_raises(self) -> None:
        stored = _stored()
        repo = mock_of[UpgradeRecommendationRepository](
            get=AsyncMock(return_value=stored),
            transition_if=AsyncMock(return_value=False),
        )
        org = mock_of[OrgMutationService](update_agent=AsyncMock())
        service = _service(repo=repo, org_mutations=org)
        with pytest.raises(UpgradeRecommendationAlreadyDecidedError):
            await service.approve(stored.id, decided_by="op")
        org.update_agent.assert_not_called()

    async def test_reject_transitions_without_reassign(self) -> None:
        stored = _stored()
        repo = mock_of[UpgradeRecommendationRepository](
            get=AsyncMock(return_value=stored),
            transition_if=AsyncMock(return_value=True),
        )
        org = mock_of[OrgMutationService](update_agent=AsyncMock())
        service = _service(repo=repo, org_mutations=org)
        await service.reject(stored.id, decided_by="op")
        org.update_agent.assert_not_called()

    async def test_reject_already_decided_raises(self) -> None:
        stored = _stored()
        repo = mock_of[UpgradeRecommendationRepository](
            get=AsyncMock(return_value=stored),
            transition_if=AsyncMock(return_value=False),
        )
        org = mock_of[OrgMutationService](update_agent=AsyncMock())
        service = _service(repo=repo, org_mutations=org)
        with pytest.raises(UpgradeRecommendationAlreadyDecidedError):
            await service.reject(stored.id, decided_by="op")
        org.update_agent.assert_not_called()

    async def test_apply_auto_reassigns_when_cas_wins(self) -> None:
        stored = _stored()
        repo = mock_of[UpgradeRecommendationRepository](
            transition_if=AsyncMock(return_value=True),
        )
        org = mock_of[OrgMutationService](update_agent=AsyncMock())
        service = _service(repo=repo, org_mutations=org)
        await service.apply_auto(stored)
        org.update_agent.assert_awaited_once()

    async def test_apply_auto_noop_when_cas_loses(self) -> None:
        stored = _stored()
        repo = mock_of[UpgradeRecommendationRepository](
            transition_if=AsyncMock(return_value=False),
        )
        org = mock_of[OrgMutationService](update_agent=AsyncMock())
        service = _service(repo=repo, org_mutations=org)
        await service.apply_auto(stored)
        org.update_agent.assert_not_called()

    async def test_reassign_tolerates_failed_agent(self) -> None:
        stored = _stored(agents=(sid("a"), sid("b")))
        repo = mock_of[UpgradeRecommendationRepository](
            get=AsyncMock(return_value=stored),
            transition_if=AsyncMock(return_value=True),
        )
        org = mock_of[OrgMutationService](
            update_agent=AsyncMock(side_effect=[RuntimeError("gone"), None]),
        )
        service = _service(repo=repo, org_mutations=org)
        # One agent fails; the apply still completes for the other.
        await service.approve(stored.id, decided_by="op")
        assert org.update_agent.await_count == 2
        # Both agents were attempted with the recommended model, in order.
        assert [call.args[0] for call in org.update_agent.await_args_list] == [
            sid("a"),
            sid("b"),
        ]
        expected = UpdateAgentOrgRequest(
            model_provider="example-provider", model_id="new"
        )
        assert all(
            call.args[1] == expected for call in org.update_agent.await_args_list
        )
