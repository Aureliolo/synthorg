# module-kind: tests
"""Unit tests for the proposer build + persistence-factory wiring."""

from datetime import UTC, datetime

import pytest
from typeguard import suppress_type_checks

from synthorg.api.app_builders import build_chief_of_staff_proposer
from synthorg.api.approval_store import ApprovalStore
from synthorg.api.conversational_builders import (
    build_conversational_actor,
    build_operator_console,
)
from synthorg.api.lifecycle_helpers.conversational_reconcile import (
    reconcile_orphaned_conversational_invites,
)
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.actor import ConversationalActor
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.enums import ConversationInviteStatus
from synthorg.meta.chief_of_staff.group_models import ConversationInvite
from synthorg.meta.chief_of_staff.operator_console import OperatorConsoleService
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.persistence.approval_protocol import ApprovalRepository
from synthorg.persistence.conversational_factory import (
    ConversationalRepositories,
    build_conversational_repositories,
)
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.meta.chief_of_staff.group_chat_fakes import FakeInviteRepo

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _pending_invite(invite_id: str) -> ConversationInvite:
    return ConversationInvite(
        id=as_uuid(invite_id),
        conversation_id=sid("conv-orphan"),
        approval_id=NotBlankStr("appr-gone"),
        requested_by_agent_id=NotBlankStr("agent-ceo"),
        target_agent_id=NotBlankStr("agent-cfo"),
        target_role=NotBlankStr("CFO"),
        reason=NotBlankStr("sign-off needed"),
        status=ConversationInviteStatus.PENDING,
        created_at=_NOW,
    )


def _reconcile_repos(invite_repo: FakeInviteRepo) -> ConversationalRepositories:
    return ConversationalRepositories(
        conversation_repo=object(),  # type: ignore[arg-type]
        turn_repo=object(),  # type: ignore[arg-type]
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
            ChiefOfStaffConfig(propose_enabled=True, propose_model="example-basic-001"),
            provider_registry=_FakeRegistry(providers=["p"]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=None,
            cost_tracker=None,
        )
        assert result is None

    def test_none_when_no_providers(self) -> None:
        result = build_chief_of_staff_proposer(
            ChiefOfStaffConfig(propose_enabled=True, propose_model="example-basic-001"),
            provider_registry=_FakeRegistry(providers=[]),  # type: ignore[arg-type]
            approval_store=ApprovalStore(),
            repositories=_repos(),
            cost_tracker=None,
        )
        assert result is None

    def test_builds_when_all_present(self) -> None:
        # A bound {provider, model_id} propose_model: the provider resolves
        # explicitly (a bare id would leave the feature unwired).
        propose_model = serialize_model_ref(
            ModelRef(provider="p", model_id="example-basic-001")
        )
        with suppress_type_checks():
            result = build_chief_of_staff_proposer(
                ChiefOfStaffConfig(propose_enabled=True, propose_model=propose_model),
                provider_registry=_FakeRegistry(providers=["p"]),  # type: ignore[arg-type]
                approval_store=ApprovalStore(),
                repositories=_repos(),
                cost_tracker=None,
            )
        assert isinstance(result, ChiefOfStaffProposer)


class TestBuildConversationalActor:
    def test_declines_naming_the_disabled_switch(self) -> None:
        with pytest.raises(SubsystemDeclinedError, match="direct_mcp_enabled is off"):
            build_conversational_actor(
                ChiefOfStaffConfig(direct_mcp_enabled=False),
                engine=_engine(has_mcp=True, has_governance=True),
                agent_registry=object(),  # type: ignore[arg-type]
                autonomy_resolver=None,
            )

    def test_declines_naming_the_missing_self_consumer(self) -> None:
        """Naming the switch would tell an org that enabled it the opposite."""
        with pytest.raises(SubsystemDeclinedError, match="no MCP self-consumer"):
            build_conversational_actor(
                ChiefOfStaffConfig(direct_mcp_enabled=True),
                engine=_engine(has_mcp=False, has_governance=True),
                agent_registry=object(),  # type: ignore[arg-type]
                autonomy_resolver=None,
            )

    def test_declines_naming_inactive_governance(self) -> None:
        # Fail-closed: direct MCP acting without governance would run
        # permitted write/admin actions with no escalate-and-park step.
        with pytest.raises(
            SubsystemDeclinedError, match="security governance is inactive"
        ):
            build_conversational_actor(
                ChiefOfStaffConfig(direct_mcp_enabled=True),
                engine=_engine(has_mcp=True, has_governance=False),
                agent_registry=object(),  # type: ignore[arg-type]
                autonomy_resolver=None,
            )

    def test_builds_when_governed(self) -> None:
        result = build_conversational_actor(
            ChiefOfStaffConfig(direct_mcp_enabled=True),
            engine=_engine(has_mcp=True, has_governance=True),
            agent_registry=mock_of[AgentRegistryService](),
            autonomy_resolver=None,
        )
        assert isinstance(result, ConversationalActor)


_CONSOLE_MODEL = serialize_model_ref(
    ModelRef(provider="p", model_id="example-basic-001")
)


class TestBuildOperatorConsole:
    def test_declines_naming_the_disabled_switch(self) -> None:
        with pytest.raises(
            SubsystemDeclinedError, match="operator_console_enabled is off"
        ):
            build_operator_console(
                ChiefOfStaffConfig(operator_console_enabled=False),
                engine=_engine(has_mcp=True, has_governance=True),
                autonomy_resolver=None,
                clock=FakeClock(),
            )

    def test_declines_naming_the_missing_self_consumer(self) -> None:
        with pytest.raises(SubsystemDeclinedError, match="no MCP self-consumer"):
            build_operator_console(
                ChiefOfStaffConfig(
                    operator_console_enabled=True,
                    operator_console_model=_CONSOLE_MODEL,
                ),
                engine=_engine(has_mcp=False, has_governance=True),
                autonomy_resolver=None,
                clock=FakeClock(),
            )

    def test_declines_naming_inactive_governance(self) -> None:
        # Fail-closed: an ungated console would run permitted write/admin
        # actions with no escalate-and-park step.
        with pytest.raises(
            SubsystemDeclinedError, match="security governance is inactive"
        ):
            build_operator_console(
                ChiefOfStaffConfig(
                    operator_console_enabled=True,
                    operator_console_model=_CONSOLE_MODEL,
                ),
                engine=_engine(has_mcp=True, has_governance=False),
                autonomy_resolver=None,
                clock=FakeClock(),
            )

    def test_declines_naming_the_unbound_model(self) -> None:
        # Fail-closed: the console cannot dispatch without an explicit
        # (provider, model) pair, so an unset model leaves it inert.
        with pytest.raises(
            SubsystemDeclinedError, match="operator_console_model is bound"
        ):
            build_operator_console(
                ChiefOfStaffConfig(operator_console_enabled=True),
                engine=_engine(has_mcp=True, has_governance=True),
                autonomy_resolver=None,
                clock=FakeClock(),
            )

    def test_builds_when_governed_and_model_bound(self) -> None:
        result = build_operator_console(
            ChiefOfStaffConfig(
                operator_console_enabled=True, operator_console_model=_CONSOLE_MODEL
            ),
            engine=_engine(has_mcp=True, has_governance=True),
            autonomy_resolver=None,
            clock=FakeClock(),
        )
        assert isinstance(result, OperatorConsoleService)


class TestReconcileOrphanedConversationalInvites:
    async def test_in_memory_store_retires_pending_orphans(self) -> None:
        # With an in-memory ApprovalStore the approval queue is empty at
        # boot, so prior-boot PENDING invites are unreachable. They are
        # retired terminal (invite -> DECLINED) so the row survives as an
        # audit record while leaving the actionable set.
        invite_repo = FakeInviteRepo()
        await invite_repo.save(_pending_invite("i-orphan"))

        await reconcile_orphaned_conversational_invites(
            _reconcile_repos(invite_repo), ApprovalStore()
        )

        retired_invite = await invite_repo.get(sid("i-orphan"))
        assert retired_invite is not None
        assert retired_invite.status is ConversationInviteStatus.DECLINED

    async def test_non_in_memory_store_leaves_orphans(self) -> None:
        # A store that is not a recognised in-memory ApprovalStore is
        # treated as persistent: its approvals survive restart, so PENDING
        # rows stay resumable and must NOT be deleted.
        invite_repo = FakeInviteRepo()
        await invite_repo.save(_pending_invite("i-keep"))

        await reconcile_orphaned_conversational_invites(
            _reconcile_repos(invite_repo),
            mock_of[ApprovalStoreProtocol](),
        )

        kept_invite = await invite_repo.get(sid("i-keep"))
        assert kept_invite is not None
        assert kept_invite.status is ConversationInviteStatus.PENDING

    async def test_persistent_repo_store_leaves_orphans(self) -> None:
        # An ApprovalStore backed by a durable repo (has_persistent_repo)
        # keeps its approvals across restart, so PENDING rows stay
        # resumable and must NOT be retired -- this is the third
        # discriminator branch (a real ApprovalStore, but persistent).
        invite_repo = FakeInviteRepo()
        await invite_repo.save(_pending_invite("i-keep"))

        await reconcile_orphaned_conversational_invites(
            _reconcile_repos(invite_repo),
            ApprovalStore(repo=mock_of[ApprovalRepository]()),
        )

        kept_invite = await invite_repo.get(sid("i-keep"))
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
