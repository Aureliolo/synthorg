# module-kind: tests
"""Unit tests for the agent-initiated invite flow.

Covers the three seams of the feature:

- :func:`parse_group_contribution` / ``GroupInviteCoordinator`` -- the
  PARK half (parse the structured envelope, enforce the bounds, write
  the invite row first then the gating approval, compensate on failure)
  plus the first-turn handover preamble.
- :func:`try_conversational_invite_resume` -- the RESUME half (accept
  adds the roster row via a single-winner CAS; decline leaves membership
  unchanged; repo-direct so it is inert for non-invite approvals).
- :class:`GroupChatService` with the coordinator wired -- the round-loop
  integration (parsed message persisted, invite surfaced, per-round cap,
  the invited agent's first prompt carries the fenced preamble).
"""

from datetime import timedelta
from typing import Any

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._conversational_resume import (
    try_conversational_invite_resume,
)
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationParticipantStatus,
)
from synthorg.meta.chief_of_staff.group_invite import (
    GroupInviteCoordinator,
    parse_group_contribution,
)
from synthorg.meta.chief_of_staff.group_models import (
    ConversationInvite,
    ConversationParticipant,
    GroupConverseArgs,
    InviteRequest,
)
from synthorg.meta.chief_of_staff.resume_service import ConversationalResumeService
from synthorg.meta.state import MetaStateSlice
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
)
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalRepository,
)
from tests._shared import FakeClock, as_uuid, make_app_state, mock_of, sid
from tests.unit.meta.chief_of_staff.group_chat_fakes import (
    FakeInviteRepo,
    FakeParticipantRepo,
    ScriptedAgentCaller,
    build_group_chat_with_invites,
)
from tests.unit.meta.chief_of_staff.propose_fakes import (
    START,
    build_registry,
    make_identity,
)

pytestmark = pytest.mark.unit

_CONV = NotBlankStr("conv-1")
_DECIDED_BY = "operator-1"
_REASON = "budget sign-off needed"


def _invite_config(**overrides: Any) -> ChiefOfStaffConfig:  # type: ignore[explicit-any]  # kwargs forwarded into ChiefOfStaffConfig constructor
    """Build a group-chat config with invites enabled.

    Returns:
        A config with ``group_chat_enabled`` and ``invite_enabled`` set,
        plus any *overrides*.
    """
    return ChiefOfStaffConfig(group_chat_enabled=True, invite_enabled=True, **overrides)


def _participant(agent_id: str, name: str, role: str) -> ConversationParticipant:
    """Build an active participant row for *agent_id*.

    Returns:
        An ``ACTIVE`` :class:`ConversationParticipant`.
    """
    return ConversationParticipant(
        id=as_uuid(f"part-{agent_id}"),
        conversation_id=_CONV,
        agent_id=NotBlankStr(agent_id),
        agent_name=NotBlankStr(name),
        participant_role=NotBlankStr(role),
        status=ConversationParticipantStatus.ACTIVE,
        added_by=NotBlankStr("user-1"),
        added_at=START,
    )


async def _coordinator_with_roster(
    config: ChiefOfStaffConfig,
    *roster: ConversationParticipant,
) -> tuple[GroupInviteCoordinator, FakeInviteRepo, FakeParticipantRepo, AppState]:
    """Build a coordinator over fresh doubles, pre-seeding *roster*.

    Returns:
        The coordinator, its invite repo, its participant repo, and an
        ``AppState`` wiring the same repos + a CEO/CFO registry so the
        resume-side assertions read the same state the park wrote.
    """
    ceo = make_identity(name="Dana", role="CEO")
    cfo = make_identity(name="Fiona", role="CFO")
    registry = await build_registry(ceo, cfo)
    invite_repo = FakeInviteRepo()
    participant_repo = FakeParticipantRepo()
    approval_store = ApprovalStore()
    coordinator = GroupInviteCoordinator(
        invite_repo=invite_repo,
        approval_store=approval_store,
        agent_registry=registry,
        participant_repo=participant_repo,
        config=config,
        clock=FakeClock(start=START),
    )
    for participant in roster:
        await participant_repo.save(participant)
    app_state = make_app_state(
        approval_store=approval_store,
        agent_registry=registry,
        clock=FakeClock(start=START),
        slices={
            MetaStateSlice: {
                "conversation_invite_repo": invite_repo,
                "conversation_participant_repo": participant_repo,
                "conversational_resume_service": _resume_service(
                    invite_repo, participant_repo
                ),
            }
        },
    )
    return coordinator, invite_repo, participant_repo, app_state


def _resume_service(
    invite_repo: FakeInviteRepo,
    participant_repo: FakeParticipantRepo,
) -> ConversationalResumeService:
    """Build the resume-service facade the invite flow routes through.

    The proposal repo is unused by the invite flow, so a typed mock
    stands in for it.

    Returns:
        The facade wrapping the test's invite + participant doubles.
    """
    return ConversationalResumeService(
        proposal_repo=mock_of[ConversationalProposalRepository](),
        invite_repo=invite_repo,
        participant_repo=participant_repo,
    )


def _invite_request(target: str = "CFO", reason: str = _REASON) -> InviteRequest:
    """Build an :class:`InviteRequest` for *target*.

    Returns:
        The parsed invite ask.
    """
    return InviteRequest(target=NotBlankStr(target), reason=NotBlankStr(reason))


class TestParseGroupContribution:
    def test_envelope_with_invite_parsed(self) -> None:
        raw = (
            '{"message": "We need finance here.", '
            '"invite": {"target": "CFO", "reason": "budget sign-off"}}'
        )
        contribution, valid = parse_group_contribution(raw)
        assert valid is True
        assert contribution.message == "We need finance here."
        assert contribution.invite is not None
        assert contribution.invite.target == "CFO"
        assert contribution.invite.reason == "budget sign-off"

    def test_envelope_without_invite_parsed(self) -> None:
        contribution, valid = parse_group_contribution(
            '{"message": "Just my view.", "invite": null}'
        )
        assert valid is True
        assert contribution.message == "Just my view."
        assert contribution.invite is None

    def test_non_json_degrades_to_plain_message(self) -> None:
        # A non-envelope reply must not drop the contribution nor park a
        # bogus invite: the raw text becomes the message.
        contribution, valid = parse_group_contribution("plain text reply")
        assert valid is False
        assert contribution.message == "plain text reply"
        assert contribution.invite is None

    def test_json_without_string_message_degrades(self) -> None:
        contribution, valid = parse_group_contribution('{"message": 42}')
        assert valid is False
        assert contribution.message == '{"message": 42}'
        assert contribution.invite is None

    def test_incomplete_invite_object_yields_no_invite(self) -> None:
        # The envelope is valid (message is a string) but the invite is
        # missing its reason, so the invite degrades to None while the
        # message still stands.
        contribution, valid = parse_group_contribution(
            '{"message": "hi", "invite": {"target": "CFO"}}'
        )
        assert valid is True
        assert contribution.message == "hi"
        assert contribution.invite is None


class TestRequestInvite:
    async def test_happy_path_parks_invite_and_approval(self) -> None:
        coordinator, invite_repo, _, _ = await _coordinator_with_roster(
            _invite_config()
        )
        summary = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=_invite_request(),
            now=START,
        )
        assert summary is not None
        assert summary.target_name == "Fiona"
        assert summary.target_role == "CFO"
        assert summary.reason == _REASON
        # Row written first: exactly one PENDING invite backs the approval.
        invites = tuple(invite_repo.items.values())
        assert len(invites) == 1
        assert invites[0].status is ConversationInviteStatus.PENDING
        assert invites[0].approval_id == summary.approval_id
        # The gating approval is the canonical conversational-invite item.
        approval = await coordinator._approval_store.get(summary.approval_id)
        assert approval is not None
        assert approval.source is ApprovalSource.CONVERSATIONAL_INVITE
        assert approval.action_type == "conversational:invite_agent"

    async def test_unknown_target_skipped(self) -> None:
        coordinator, invite_repo, _, _ = await _coordinator_with_roster(
            _invite_config()
        )
        summary = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=_invite_request(target="Nobody"),
            now=START,
        )
        assert summary is None
        assert invite_repo.items == {}

    async def test_already_participant_skipped(self) -> None:
        coordinator, invite_repo, participant_repo, _ = await _coordinator_with_roster(
            _invite_config()
        )
        # Seed the target as already-active so the invite is a no-op. The
        # roster row must carry the SAME id the registry resolves "Fiona"
        # to, else the membership check cannot match.
        cfo = await coordinator._agent_registry.get_by_name(NotBlankStr("Fiona"))
        assert cfo is not None
        await participant_repo.save(_participant(str(cfo.id), "Fiona", "CFO"))
        summary = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=_invite_request(target="Fiona"),
            now=START,
        )
        assert summary is None
        assert invite_repo.items == {}

    async def test_at_capacity_skipped(self) -> None:
        # Cap the room at two and fill it, so a third invite trips
        # ``at_capacity`` before any row is written.
        coordinator, invite_repo, _, _ = await _coordinator_with_roster(
            _invite_config(group_chat_max_participants=2),
            _participant("a", "Alice", "COO"),
            _participant("b", "Bob", "CTO"),
        )
        summary = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("a"),
            requested_by_name=NotBlankStr("Alice"),
            invite_request=_invite_request(),
            now=START,
        )
        assert summary is None
        assert invite_repo.items == {}

    async def test_duplicate_pending_skipped(self) -> None:
        coordinator, invite_repo, _, _ = await _coordinator_with_roster(
            _invite_config()
        )
        request = _invite_request()
        first = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=request,
            now=START,
        )
        second = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=request,
            now=START,
        )
        assert first is not None
        assert second is None
        # Only the first invite was ever written.
        assert len(invite_repo.items) == 1

    async def test_park_failure_compensates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        coordinator, invite_repo, _, _ = await _coordinator_with_roster(
            _invite_config()
        )

        async def _boom(_item: ApprovalItem) -> None:
            msg = "approval store down"
            raise RuntimeError(msg)

        monkeypatch.setattr(coordinator._approval_store, "add", _boom)
        summary = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=_invite_request(),
            now=START,
        )
        # Approval add failed -> the row-first invite is cleaned up so no
        # orphan invite outlives the failed park.
        assert summary is None
        assert invite_repo.items == {}


class TestInvitedPreamble:
    async def _build(
        self,
    ) -> tuple[GroupInviteCoordinator, FakeInviteRepo, NotBlankStr]:
        ceo = make_identity(name="Dana", role="CEO")
        registry = await build_registry(ceo)
        invite_repo = FakeInviteRepo()
        coordinator = GroupInviteCoordinator(
            invite_repo=invite_repo,
            approval_store=ApprovalStore(),
            agent_registry=registry,
            participant_repo=FakeParticipantRepo(),
            config=_invite_config(),
            clock=FakeClock(start=START),
        )
        return coordinator, invite_repo, NotBlankStr(str(ceo.id))

    async def _seed_accepted(
        self, invite_repo: FakeInviteRepo, requested_by: str
    ) -> None:
        await invite_repo.save(
            ConversationInvite(
                id=as_uuid("inv-1"),
                conversation_id=_CONV,
                approval_id=NotBlankStr("appr-1"),
                requested_by_agent_id=NotBlankStr(requested_by),
                target_agent_id=NotBlankStr("cfo-id"),
                target_role=NotBlankStr("CFO"),
                reason=NotBlankStr(_REASON),
                status=ConversationInviteStatus.ACCEPTED,
                created_at=START,
            )
        )

    async def test_already_spoke_returns_none(self) -> None:
        coordinator, invite_repo, ceo_id = await self._build()
        await self._seed_accepted(invite_repo, ceo_id)
        preamble = await coordinator.invited_preamble(
            _CONV, NotBlankStr("cfo-id"), already_spoke=True
        )
        assert preamble is None

    async def test_no_accepted_invite_returns_none(self) -> None:
        coordinator, _, _ = await self._build()
        preamble = await coordinator.invited_preamble(
            _CONV, NotBlankStr("cfo-id"), already_spoke=False
        )
        assert preamble is None

    async def test_accepted_invite_renders_fenced_preamble(self) -> None:
        coordinator, invite_repo, ceo_id = await self._build()
        await self._seed_accepted(invite_repo, ceo_id)
        preamble = await coordinator.invited_preamble(
            _CONV, NotBlankStr("cfo-id"), already_spoke=False
        )
        assert preamble is not None
        assert TAG_TASK_DATA in preamble
        assert "Dana" in preamble
        assert _REASON in preamble

    async def test_unregistered_inviter_falls_back_to_id(self) -> None:
        coordinator, invite_repo, _ = await self._build()
        # An inviter id with no registry identity still yields a preamble
        # (degrading the label to the id) rather than dropping the handover.
        await self._seed_accepted(invite_repo, "ghost-id")
        preamble = await coordinator.invited_preamble(
            _CONV, NotBlankStr("cfo-id"), already_spoke=False
        )
        assert preamble is not None
        assert "ghost-id" in preamble
        assert _REASON in preamble


class TestInviteResume:
    async def _park(
        self,
    ) -> tuple[AppState, FakeInviteRepo, FakeParticipantRepo, NotBlankStr]:
        (
            coordinator,
            invite_repo,
            participant_repo,
            app_state,
        ) = await _coordinator_with_roster(_invite_config())
        summary = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=_invite_request(),
            now=START,
        )
        assert summary is not None
        return app_state, invite_repo, participant_repo, summary.approval_id

    async def _added_fiona(
        self, participant_repo: FakeParticipantRepo
    ) -> list[ConversationParticipant]:
        roster = await participant_repo.query(
            ConversationParticipantFilterSpec(conversation_id=_CONV)
        )
        return [p for p in roster if p.agent_name == "Fiona"]

    async def test_non_invite_source_is_inert(self) -> None:
        store = ApprovalStore()
        await store.add(
            ApprovalItem(
                id=as_uuid("a-other"),
                action_type=NotBlankStr("review:gate"),
                title=NotBlankStr("t"),
                description=NotBlankStr("d"),
                requested_by=NotBlankStr("user-1"),
                risk_level=ApprovalRiskLevel.LOW,
                source=ApprovalSource.REVIEW_GATE,
                status=ApprovalStatus.PENDING,
                created_at=START,
            )
        )
        app_state = make_app_state(approval_store=store, clock=FakeClock(start=START))
        handled = await try_conversational_invite_resume(
            app_state, sid("a-other"), approved=True, decided_by=_DECIDED_BY
        )
        assert handled is False

    async def test_approve_adds_participant(self) -> None:
        app_state, invite_repo, participant_repo, approval_id = await self._park()
        handled = await try_conversational_invite_resume(
            app_state, approval_id, approved=True, decided_by=_DECIDED_BY
        )
        assert handled is True
        invite = next(iter(invite_repo.items.values()))
        assert invite.status is ConversationInviteStatus.ACCEPTED
        added = await self._added_fiona(participant_repo)
        assert len(added) == 1
        assert added[0].status is ConversationParticipantStatus.ACTIVE
        assert added[0].added_by == _DECIDED_BY

    async def test_double_approve_is_idempotent(self) -> None:
        app_state, _, participant_repo, approval_id = await self._park()
        first = await try_conversational_invite_resume(
            app_state, approval_id, approved=True, decided_by=_DECIDED_BY
        )
        second = await try_conversational_invite_resume(
            app_state, approval_id, approved=True, decided_by=_DECIDED_BY
        )
        assert first is True
        assert second is True
        # The single-winner CAS means only the first approve adds a row.
        assert len(await self._added_fiona(participant_repo)) == 1

    async def test_second_approval_capped_at_accept(self) -> None:
        # Two invites for DIFFERENT agents both clear the park-time
        # capacity guard against the same pre-round roster (no roster row
        # is written until accept), so the accept-time re-check is the
        # only thing stopping the second approval from pushing the room
        # one over ``group_chat_max_participants``. Both the park and the
        # accept guard read the same default cap (the coordinator's config
        # and the resume path's settings load both fall back to it), so
        # the room is seated to one below the cap to leave room for
        # exactly one of the two pending invites.
        cap = ChiefOfStaffConfig().group_chat_max_participants
        ceo = make_identity(name="Dana", role="CEO")
        cfo = make_identity(name="Fiona", role="CFO")
        cto = make_identity(name="Greg", role="CTO")
        registry = await build_registry(ceo, cfo, cto)
        invite_repo = FakeInviteRepo()
        participant_repo = FakeParticipantRepo()
        approval_store = ApprovalStore()
        coordinator = GroupInviteCoordinator(
            invite_repo=invite_repo,
            approval_store=approval_store,
            agent_registry=registry,
            participant_repo=participant_repo,
            config=_invite_config(),
            clock=FakeClock(start=START),
        )
        for seat in range(cap - 1):
            await participant_repo.save(
                _participant(f"seat-{seat}", f"Seat{seat}", "VP")
            )
        app_state = make_app_state(
            approval_store=approval_store,
            agent_registry=registry,
            clock=FakeClock(start=START),
            slices={
                MetaStateSlice: {
                    "conversation_invite_repo": invite_repo,
                    "conversation_participant_repo": participant_repo,
                    "conversational_resume_service": _resume_service(
                        invite_repo, participant_repo
                    ),
                }
            },
        )
        first = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=_invite_request(target="Fiona"),
            now=START,
        )
        second = await coordinator.request_invite(
            conversation_id=_CONV,
            requested_by_agent_id=NotBlankStr("ceo-id"),
            requested_by_name=NotBlankStr("Dana"),
            invite_request=_invite_request(target="Greg"),
            now=START,
        )
        # Both park against the same pre-round roster (one below the cap).
        assert first is not None
        assert second is not None
        first_handled = await try_conversational_invite_resume(
            app_state, first.approval_id, approved=True, decided_by=_DECIDED_BY
        )
        second_handled = await try_conversational_invite_resume(
            app_state, second.approval_id, approved=True, decided_by=_DECIDED_BY
        )
        # Both approvals are handled (the invites are marked accepted),
        # but only the first seats its agent.
        assert first_handled is True
        assert second_handled is True
        active = await participant_repo.query(
            ConversationParticipantFilterSpec(
                conversation_id=_CONV,
                status=ConversationParticipantStatus.ACTIVE,
            )
        )
        assert len(active) == cap
        names = {p.agent_name for p in active}
        assert "Fiona" in names
        assert "Greg" not in names

    async def test_decline_leaves_membership_unchanged(self) -> None:
        app_state, invite_repo, participant_repo, approval_id = await self._park()
        handled = await try_conversational_invite_resume(
            app_state, approval_id, approved=False, decided_by=_DECIDED_BY
        )
        assert handled is True
        invite = next(iter(invite_repo.items.values()))
        assert invite.status is ConversationInviteStatus.DECLINED
        assert await self._added_fiona(participant_repo) == []

    async def test_missing_invite_repo_raises(self) -> None:
        # A CONVERSATIONAL_INVITE approval landing where the invite repo
        # was never wired is a hard misconfiguration: the gate must raise
        # rather than silently mark the approval handled.
        store = ApprovalStore()
        await store.add(
            ApprovalItem(
                id=as_uuid("a-inv"),
                action_type=NotBlankStr("conversational:invite_agent"),
                title=NotBlankStr("t"),
                description=NotBlankStr("d"),
                requested_by=NotBlankStr("ceo-id"),
                risk_level=ApprovalRiskLevel.MEDIUM,
                source=ApprovalSource.CONVERSATIONAL_INVITE,
                status=ApprovalStatus.PENDING,
                created_at=START,
            )
        )
        app_state = make_app_state(approval_store=store, clock=FakeClock(start=START))
        with pytest.raises(ServiceUnavailableError):
            await try_conversational_invite_resume(
                app_state, sid("a-inv"), approved=True, decided_by=_DECIDED_BY
            )


class TestGroupChatInviteIntegration:
    async def test_invite_envelope_parks_and_message_persisted(self) -> None:
        ceo = make_identity(name="Dana", role="CEO")
        cfo = make_identity(name="Fiona", role="CFO")
        registry = await build_registry(ceo, cfo)
        caller = ScriptedAgentCaller(
            {
                str(ceo.id): (
                    '{"message": "We should bring in finance.", '
                    '"invite": {"target": "CFO", "reason": "budget sign-off"}}'
                )
            }
        )
        service, _, _, _, invite_repo, approval_store, _ = (
            build_group_chat_with_invites(agent_caller=caller, registry=registry)
        )
        result = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr("Kick-off"),
                created_by=NotBlankStr("user-1"),
                participants=(NotBlankStr(str(ceo.id)),),
            )
        )
        # The parsed message (not the raw JSON envelope) is the turn.
        assert len(result.contributions) == 1
        assert result.contributions[0].content == "We should bring in finance."
        # The invite is surfaced and a PENDING row + approval back it.
        assert len(result.pending_invites) == 1
        assert result.pending_invites[0].target_name == "Fiona"
        invites = tuple(invite_repo.items.values())
        assert len(invites) == 1
        assert invites[0].status is ConversationInviteStatus.PENDING
        approval = await approval_store.get(result.pending_invites[0].approval_id)
        assert approval is not None
        assert approval.source is ApprovalSource.CONVERSATIONAL_INVITE

    async def test_per_round_cap_limits_parked_invites(self) -> None:
        # Two agents each request an invite, but the per-round cap is 1,
        # so only the first is parked.
        ceo = make_identity(name="Dana", role="CEO")
        coo = make_identity(name="Carl", role="COO")
        cfo = make_identity(name="Fiona", role="CFO")
        cto = make_identity(name="Tariq", role="CTO")
        registry = await build_registry(ceo, coo, cfo, cto)
        caller = ScriptedAgentCaller(
            {
                str(ceo.id): (
                    '{"message": "need finance", '
                    '"invite": {"target": "CFO", "reason": "budget"}}'
                ),
                str(coo.id): (
                    '{"message": "need eng", '
                    '"invite": {"target": "CTO", "reason": "tech"}}'
                ),
            }
        )
        service, _, _, _, invite_repo, _, _ = build_group_chat_with_invites(
            agent_caller=caller,
            registry=registry,
            config=_invite_config(invite_max_per_round=1),
        )
        result = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr("Kick-off"),
                created_by=NotBlankStr("user-1"),
                participants=(NotBlankStr(str(ceo.id)), NotBlankStr(str(coo.id))),
            )
        )
        assert len(result.pending_invites) == 1
        assert len(invite_repo.items) == 1

    async def test_invited_agent_first_prompt_carries_preamble(self) -> None:
        # Full handover: CEO opens alone and speaks (round 0); consent then
        # admits CFO with an accepted invite; on round 1 CFO takes its
        # genuine first turn and its prompt carries the fenced preamble
        # (prepended above the transcript), while the established CEO's
        # does not.
        ceo = make_identity(name="Dana", role="CEO")
        cfo = make_identity(name="Fiona", role="CFO")
        registry = await build_registry(ceo, cfo)
        caller = ScriptedAgentCaller(
            {
                str(ceo.id): '{"message": "ceo view", "invite": null}',
                str(cfo.id): '{"message": "cfo view", "invite": null}',
            }
        )
        service, _, _, participant_repo, invite_repo, _, _ = (
            build_group_chat_with_invites(agent_caller=caller, registry=registry)
        )
        opened = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr("Turn 0"),
                created_by=NotBlankStr("user-1"),
                participants=(NotBlankStr(str(ceo.id)),),
            )
        )
        conversation_id = opened.conversation_id
        # Simulate granted consent: CFO joins with an accepted invite that
        # records who brought them in and why.
        await invite_repo.save(
            ConversationInvite(
                id=as_uuid("inv-1"),
                conversation_id=conversation_id,
                approval_id=NotBlankStr("appr-1"),
                requested_by_agent_id=NotBlankStr(str(ceo.id)),
                target_agent_id=NotBlankStr(str(cfo.id)),
                target_role=NotBlankStr("CFO"),
                reason=NotBlankStr(_REASON),
                status=ConversationInviteStatus.ACCEPTED,
                created_at=START,
            )
        )
        await participant_repo.save(
            ConversationParticipant(
                id=as_uuid("part-cfo"),
                conversation_id=conversation_id,
                agent_id=NotBlankStr(str(cfo.id)),
                agent_name=NotBlankStr("Fiona"),
                participant_role=NotBlankStr("CFO"),
                status=ConversationParticipantStatus.ACTIVE,
                added_by=NotBlankStr(_DECIDED_BY),
                added_at=START + timedelta(seconds=1),
            )
        )
        caller.calls.clear()
        await service.converse(
            GroupConverseArgs(
                message=NotBlankStr("Turn 1"),
                created_by=NotBlankStr("user-1"),
                conversation_id=conversation_id,
            )
        )
        prompts = {agent_id: prompt for agent_id, prompt, _, _ in caller.calls}
        cfo_prompt = prompts[str(cfo.id)]
        ceo_prompt = prompts[str(ceo.id)]
        # CFO's genuine first turn carries the fenced inviter+reason
        # handover, prepended above the transcript.
        assert _REASON in cfo_prompt
        assert "Dana" in cfo_prompt
        assert TAG_TASK_DATA in cfo_prompt
        assert cfo_prompt.index(_REASON) < cfo_prompt.index("## Conversation so far")
        # The established CEO (already spoke in round 0) never re-sees it.
        assert _REASON not in ceo_prompt
