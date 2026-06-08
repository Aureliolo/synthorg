# module-kind: tests
"""Unit tests for the proposer build + persistence-factory wiring."""

from datetime import UTC, datetime

import pytest
from typeguard import suppress_type_checks

from synthorg.api.app_builders import build_chief_of_staff_proposer
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.conversational_builders import build_conversational_actor
from synthorg.api.lifecycle_helpers.conversational_reconcile import (
    reconcile_orphaned_conversational_intake,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.communication.conversation.enums import ConversationalProposalStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.actor import ConversationalActor
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.enums import ConversationInviteStatus
from synthorg.meta.chief_of_staff.group_models import ConversationInvite
from synthorg.meta.chief_of_staff.models import ConversationalProposal
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.persistence.approval_protocol import ApprovalRepository
from synthorg.persistence.conversational_factory import (
    ConversationalRepositories,
    build_conversational_repositories,
)
from tests._shared import mock_of
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.meta.chief_of_staff.group_chat_fakes import FakeInviteRepo
from tests.unit.meta.chief_of_staff.propose_fakes import FakeProposalRepo

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _pending_proposal(proposal_id: str) -> ConversationalProposal:
    return ConversationalProposal(
        id=proposal_id,
        conversation_id="conv-orphan",
        approval_id="appr-gone",
        work_item_json="{}",
        status=ConversationalProposalStatus.PENDING,
        created_at=_NOW,
    )


def _pending_invite(invite_id: str) -> ConversationInvite:
    return ConversationInvite(
        id=NotBlankStr(invite_id),
        conversation_id=NotBlankStr("conv-orphan"),
        approval_id=NotBlankStr("appr-gone"),
        requested_by_agent_id=NotBlankStr("agent-ceo"),
        target_agent_id=NotBlankStr("agent-cfo"),
        target_role=NotBlankStr("CFO"),
        reason=NotBlankStr("sign-off needed"),
        status=ConversationInviteStatus.PENDING,
        created_at=_NOW,
    )


def _reconcile_repos(
    proposal_repo: FakeProposalRepo, invite_repo: FakeInviteRepo
) -> ConversationalRepositories:
    return ConversationalRepositories(
        conversation_repo=object(),  # type: ignore[arg-type]
        turn_repo=object(),  # type: ignore[arg-type]
        proposal_repo=proposal_repo,
        participant_repo=object(),  # type: ignore[arg-type]
        invite_repo=invite_repo,
    )


def _engine(*, has_mcp: bool, has_governance: bool) -> AgentEngine:
    """Build an ``AgentEngine`` double exposing only the read gates."""
    engine: AgentEngine = mock_of[AgentEngine](
        has_mcp_self_consumer=has_mcp,
        has_security_governance=has_governance,
    )
    return engine


class _FakeRegistry:
    """Minimal ``ProviderRegistry`` surface used by the builder."""

    def __init__(self, *, providers: list[str]) -> None:
        self._providers = providers
        self._provider = ScriptedProvider(responses=[])

    def list_providers(self) -> list[str]:
        return self._providers

    def get(self, name: str) -> ScriptedProvider:
        del name
        return self._provider


def _repos() -> ConversationalRepositories:
    # The builder only stores these references; behaviour is covered by
    # the proposer + conformance suites, so opaque sentinels suffice.
    return ConversationalRepositories(
        conversation_repo=object(),  # type: ignore[arg-type]
        turn_repo=object(),  # type: ignore[arg-type]
        proposal_repo=object(),  # type: ignore[arg-type]
        participant_repo=object(),  # type: ignore[arg-type]
        invite_repo=object(),  # type: ignore[arg-type]
    )


class TestBuildChiefOfStaffProposer:
    def test_none_when_disabled(self) -> None:
        result = build_chief_of_staff_proposer(
            ChiefOfStaffConfig(propose_enabled=False),
            provider_registry=_FakeRegistry(providers=["p"]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=_repos(),
            cost_tracker=None,
        )
        assert result is None

    def test_none_when_no_repositories(self) -> None:
        result = build_chief_of_staff_proposer(
            ChiefOfStaffConfig(propose_enabled=True),
            provider_registry=_FakeRegistry(providers=["p"]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=None,
            cost_tracker=None,
        )
        assert result is None

    def test_none_when_no_providers(self) -> None:
        result = build_chief_of_staff_proposer(
            ChiefOfStaffConfig(propose_enabled=True),
            provider_registry=_FakeRegistry(providers=[]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=_repos(),
            cost_tracker=None,
        )
        assert result is None

    def test_builds_when_all_present(self) -> None:
        with suppress_type_checks():
            result = build_chief_of_staff_proposer(
                ChiefOfStaffConfig(propose_enabled=True),
                provider_registry=_FakeRegistry(providers=["p"]),  # type: ignore[arg-type]
                approval_store=ApprovalStore(),
                repositories=_repos(),
                cost_tracker=None,
            )
        assert isinstance(result, ChiefOfStaffProposer)


class TestBuildConversationalActor:
    def test_none_when_disabled(self) -> None:
        result = build_conversational_actor(
            ChiefOfStaffConfig(direct_mcp_enabled=False),
            engine=_engine(has_mcp=True, has_governance=True),
            agent_registry=object(),  # type: ignore[arg-type]
            autonomy_resolver=None,
        )
        assert result is None

    def test_none_when_no_mcp_self_consumer(self) -> None:
        result = build_conversational_actor(
            ChiefOfStaffConfig(direct_mcp_enabled=True),
            engine=_engine(has_mcp=False, has_governance=True),
            agent_registry=object(),  # type: ignore[arg-type]
            autonomy_resolver=None,
        )
        assert result is None

    def test_none_when_governance_inactive(self) -> None:
        # Fail-closed: direct MCP acting without governance would run
        # permitted write/admin actions with no escalate-and-park step.
        result = build_conversational_actor(
            ChiefOfStaffConfig(direct_mcp_enabled=True),
            engine=_engine(has_mcp=True, has_governance=False),
            agent_registry=object(),  # type: ignore[arg-type]
            autonomy_resolver=None,
        )
        assert result is None

    def test_builds_when_governed(self) -> None:
        result = build_conversational_actor(
            ChiefOfStaffConfig(direct_mcp_enabled=True),
            engine=_engine(has_mcp=True, has_governance=True),
            agent_registry=mock_of[AgentRegistryService](),
            autonomy_resolver=None,
        )
        assert isinstance(result, ConversationalActor)


class TestReconcileOrphanedConversationalIntake:
    async def test_in_memory_store_retires_pending_orphans(self) -> None:
        # With an in-memory ApprovalStore the approval queue is empty at
        # boot, so prior-boot PENDING proposals/invites are unreachable.
        # They are retired terminal (proposal -> REJECTED, invite ->
        # DECLINED) so the row survives as an audit record while leaving
        # the actionable set; a terminal proposal is left untouched.
        proposal_repo = FakeProposalRepo()
        await proposal_repo.save(_pending_proposal("p-orphan"))
        executed = _pending_proposal("p-done").model_copy(
            update={"status": ConversationalProposalStatus.EXECUTED}
        )
        await proposal_repo.save(executed)
        invite_repo = FakeInviteRepo()
        await invite_repo.save(_pending_invite("i-orphan"))

        await reconcile_orphaned_conversational_intake(
            _reconcile_repos(proposal_repo, invite_repo), ApprovalStore()
        )

        orphan = await proposal_repo.get("p-orphan")
        assert orphan is not None
        assert orphan.status is ConversationalProposalStatus.REJECTED
        done = await proposal_repo.get("p-done")
        assert done is not None
        assert done.status is ConversationalProposalStatus.EXECUTED
        retired_invite = await invite_repo.get("i-orphan")
        assert retired_invite is not None
        assert retired_invite.status is ConversationInviteStatus.DECLINED

    async def test_non_in_memory_store_leaves_orphans(self) -> None:
        # A store that is not a recognised in-memory ApprovalStore is
        # treated as persistent: its approvals survive restart, so PENDING
        # rows stay resumable and must NOT be deleted.
        proposal_repo = FakeProposalRepo()
        await proposal_repo.save(_pending_proposal("p-keep"))
        invite_repo = FakeInviteRepo()
        await invite_repo.save(_pending_invite("i-keep"))

        await reconcile_orphaned_conversational_intake(
            _reconcile_repos(proposal_repo, invite_repo),
            mock_of[ApprovalStoreProtocol](),
        )

        kept_proposal = await proposal_repo.get("p-keep")
        assert kept_proposal is not None
        assert kept_proposal.status is ConversationalProposalStatus.PENDING
        kept_invite = await invite_repo.get("i-keep")
        assert kept_invite is not None
        assert kept_invite.status is ConversationInviteStatus.PENDING

    async def test_persistent_repo_store_leaves_orphans(self) -> None:
        # An ApprovalStore backed by a durable repo (has_persistent_repo)
        # keeps its approvals across restart, so PENDING rows stay
        # resumable and must NOT be retired -- this is the third
        # discriminator branch (a real ApprovalStore, but persistent).
        proposal_repo = FakeProposalRepo()
        await proposal_repo.save(_pending_proposal("p-keep"))
        invite_repo = FakeInviteRepo()
        await invite_repo.save(_pending_invite("i-keep"))

        await reconcile_orphaned_conversational_intake(
            _reconcile_repos(proposal_repo, invite_repo),
            ApprovalStore(repo=mock_of[ApprovalRepository]()),
        )

        kept_proposal = await proposal_repo.get("p-keep")
        assert kept_proposal is not None
        assert kept_proposal.status is ConversationalProposalStatus.PENDING
        kept_invite = await invite_repo.get("i-keep")
        assert kept_invite is not None
        assert kept_invite.status is ConversationInviteStatus.PENDING


class TestBuildConversationalRepositories:
    def test_none_when_backend_absent(self) -> None:
        assert build_conversational_repositories(None) is None

    def test_none_when_not_connected(self) -> None:
        class _Disconnected:
            is_connected = False
            backend_name = "sqlite"

            def get_db(self) -> object:
                return object()

        assert (
            build_conversational_repositories(_Disconnected())  # type: ignore[arg-type]
            is None
        )

    def test_none_when_unknown_backend(self) -> None:
        class _Unknown:
            is_connected = True
            backend_name = "mysql"

            def get_db(self) -> object:
                return object()

        assert (
            build_conversational_repositories(_Unknown())  # type: ignore[arg-type]
            is None
        )
