"""Tests for EvolutionService orchestration."""

from datetime import date
from typing import cast
from unittest.mock import AsyncMock, MagicMock, PropertyMock
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.evolution.config import (
    AdapterConfig,
    EvolutionConfig,
)
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationDecision,
    AdaptationProposal,
    AdaptationSource,
)
from synthorg.engine.evolution.protocols import (
    AdaptationAdapter,
    AdaptationGuard,
    AdaptationProposer,
    EvolutionOutcomeSink,
)
from synthorg.engine.evolution.service import EvolutionService
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.seniority import SeniorityLevel

_AGENT_ID = str(uuid4())


def _make_identity() -> AgentIdentity:
    from uuid import UUID

    return AgentIdentity(
        id=UUID(_AGENT_ID),
        name="test-agent",
        role="test-role",
        department="engineering",
        level=SeniorityLevel.MID,
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _make_proposal(
    *,
    axis: AdaptationAxis = AdaptationAxis.PROMPT_TEMPLATE,
    confidence: float = 0.8,
) -> AdaptationProposal:
    return AdaptationProposal(
        agent_id=_AGENT_ID,
        axis=axis,
        description="test proposal",
        confidence=confidence,
        source=AdaptationSource.SUCCESS,
    )


def _make_decision(
    proposal: AdaptationProposal,
    *,
    approved: bool = True,
) -> AdaptationDecision:
    return AdaptationDecision(
        proposal_id=proposal.id,
        approved=approved,
        guard_name="test_guard",
        reason="test reason",
    )


def _make_service(  # noqa: PLR0913 -- keyword-only test collaborator DI
    *,
    proposals: tuple[AdaptationProposal, ...] = (),
    guard_approves: bool = True,
    config: EvolutionConfig | None = None,
    identity_store: AsyncMock | None = None,
    extra_adapters: dict[AdaptationAxis, object] | None = None,
    outcome_sink: object | None = None,
) -> EvolutionService:
    """Build an EvolutionService with mocked dependencies."""
    if config is None:
        # Tests in this module exercise the full pipeline by default;
        # the kill-switch ``enabled`` defaults to False on a fresh
        # install, so opt in explicitly here. Tests that want the
        # disabled-no-op path pass ``EvolutionConfig(enabled=False)``.
        config = EvolutionConfig(enabled=True)

    store = identity_store or AsyncMock()
    if identity_store is None:
        store.get_current = AsyncMock(return_value=_make_identity())
        store.list_versions = AsyncMock(return_value=())

    tracker = MagicMock(spec=PerformanceTracker)
    tracker.get_snapshot = AsyncMock(return_value=None)
    tracker.get_task_metrics = MagicMock(return_value=())

    proposer = AsyncMock(spec=AdaptationProposer)
    proposer.name = "test_proposer"
    proposer.propose = AsyncMock(return_value=proposals)

    guard = AsyncMock(spec=AdaptationGuard)
    guard.name = "test_guard"

    def _make_guard_decision(proposal: AdaptationProposal) -> AdaptationDecision:
        return _make_decision(proposal, approved=guard_approves)

    guard.evaluate = AsyncMock(side_effect=_make_guard_decision)

    adapter = AsyncMock(spec=AdaptationAdapter)
    adapter.name = "test_adapter"
    # ``axis`` is a protocol @property; PropertyMock makes the spec'd
    # attribute return the concrete enum instead of a child mock.
    type(adapter).axis = PropertyMock(
        return_value=AdaptationAxis.PROMPT_TEMPLATE,
    )
    adapter.apply = AsyncMock()

    adapters: dict[AdaptationAxis, object] = {
        AdaptationAxis.PROMPT_TEMPLATE: adapter,
        AdaptationAxis.STRATEGY_SELECTION: adapter,
    }
    if extra_adapters:
        adapters.update(extra_adapters)

    return EvolutionService(
        identity_store=store,
        tracker=tracker,
        proposer=proposer,
        guard=guard,
        adapters=cast(dict[AdaptationAxis, AdaptationAdapter], adapters),
        outcome_sink=cast("EvolutionOutcomeSink | None", outcome_sink),
        config=config,
    )


class TestEvolutionServiceEvolve:
    """EvolutionService.evolve() orchestrates the pipeline."""

    @pytest.mark.unit
    async def test_disabled_returns_empty(self) -> None:
        config = EvolutionConfig(enabled=False)
        service = _make_service(config=config)
        events = await service.evolve(agent_id=_AGENT_ID)
        assert events == ()

    @pytest.mark.unit
    async def test_no_proposals_returns_empty(self) -> None:
        service = _make_service(proposals=())
        events = await service.evolve(agent_id=_AGENT_ID)
        assert events == ()

    @pytest.mark.unit
    async def test_approved_proposal_applied(self) -> None:
        proposal = _make_proposal()
        service = _make_service(
            proposals=(proposal,),
            guard_approves=True,
        )
        events = await service.evolve(agent_id=_AGENT_ID)
        assert len(events) == 1
        assert events[0].applied is True

    @pytest.mark.unit
    async def test_rejected_proposal_not_applied(self) -> None:
        proposal = _make_proposal()
        service = _make_service(
            proposals=(proposal,),
            guard_approves=False,
        )
        events = await service.evolve(agent_id=_AGENT_ID)
        assert len(events) == 1
        assert events[0].applied is False

    @pytest.mark.unit
    async def test_outcome_sink_records_each_event(self) -> None:
        """Each processed proposal's terminal outcome reaches the sink."""
        sink = AsyncMock(spec=EvolutionOutcomeSink)
        proposal = _make_proposal()
        service = _make_service(
            proposals=(proposal,),
            guard_approves=True,
            outcome_sink=sink,
        )
        events = await service.evolve(agent_id=_AGENT_ID)
        assert events[0].applied is True
        sink.record.assert_awaited_once()
        kwargs = sink.record.await_args.kwargs
        assert kwargs["agent_id"] == _AGENT_ID
        assert kwargs["axis"] == AdaptationAxis.PROMPT_TEMPLATE.value
        assert kwargs["applied"] is True

    @pytest.mark.unit
    async def test_outcome_sink_failure_does_not_block(self) -> None:
        """A sink failure is swallowed; evolve still returns its events."""
        sink = AsyncMock(spec=EvolutionOutcomeSink)
        sink.record = AsyncMock(side_effect=RuntimeError("durable down"))
        proposal = _make_proposal()
        service = _make_service(
            proposals=(proposal,),
            guard_approves=True,
            outcome_sink=sink,
        )
        events = await service.evolve(agent_id=_AGENT_ID)
        assert len(events) == 1
        assert events[0].applied is True

    @pytest.mark.unit
    async def test_disabled_axis_rejected(self) -> None:
        """Identity axis is OFF by default."""
        proposal = _make_proposal(axis=AdaptationAxis.IDENTITY)
        service = _make_service(proposals=(proposal,))
        events = await service.evolve(agent_id=_AGENT_ID)
        assert len(events) == 1
        assert events[0].applied is False
        assert "disabled" in str(events[0].decision.reason)

    @pytest.mark.unit
    async def test_identity_axis_when_enabled(self) -> None:
        from datetime import UTC, datetime

        from synthorg.versioning.models import VersionSnapshot

        proposal = _make_proposal(axis=AdaptationAxis.IDENTITY)
        config = EvolutionConfig(
            enabled=True,
            adapters=AdapterConfig(identity=True),
        )
        identity = _make_identity()
        snap_v1 = VersionSnapshot(
            entity_id=_AGENT_ID,
            version=1,
            content_hash="a" * 64,
            snapshot=identity,
            saved_by="test",
            saved_at=datetime.now(UTC),
        )
        snap_v2 = VersionSnapshot(
            entity_id=_AGENT_ID,
            version=2,
            content_hash="b" * 64,
            snapshot=identity,
            saved_by="test",
            saved_at=datetime.now(UTC),
        )
        store = AsyncMock()
        store.get_current = AsyncMock(return_value=identity)
        # First call returns v1 (before), second returns v2 (after).
        store.list_versions = AsyncMock(
            side_effect=[(snap_v1,), (snap_v2,)],
        )

        adapter = AsyncMock(spec=AdaptationAdapter)
        adapter.apply = AsyncMock()
        type(adapter).axis = PropertyMock(
            return_value=AdaptationAxis.IDENTITY,
        )
        service = _make_service(
            proposals=(proposal,),
            guard_approves=True,
            config=config,
            identity_store=store,
            extra_adapters={AdaptationAxis.IDENTITY: adapter},
        )

        events = await service.evolve(agent_id=_AGENT_ID)
        assert len(events) == 1
        assert events[0].applied is True
        assert events[0].identity_version_before == 1
        assert events[0].identity_version_after == 2

    @pytest.mark.unit
    async def test_multiple_proposals(self) -> None:
        p1 = _make_proposal(axis=AdaptationAxis.PROMPT_TEMPLATE)
        p2 = _make_proposal(axis=AdaptationAxis.STRATEGY_SELECTION)
        service = _make_service(
            proposals=(p1, p2),
            guard_approves=True,
        )
        events = await service.evolve(agent_id=_AGENT_ID)
        assert len(events) == 2
        assert all(e.applied for e in events)

    @pytest.mark.unit
    async def test_adapter_error_produces_unapplied_event(self) -> None:
        proposal = _make_proposal()
        service = _make_service(
            proposals=(proposal,),
            guard_approves=True,
        )
        # Make adapter raise.
        adapter_mock = cast(
            AsyncMock,
            service._adapters[AdaptationAxis.PROMPT_TEMPLATE],
        )
        adapter_mock.apply.side_effect = RuntimeError("boom")

        events = await service.evolve(agent_id=_AGENT_ID)
        assert len(events) == 1
        assert events[0].applied is False

    @pytest.mark.unit
    async def test_context_build_failure_returns_empty(self) -> None:
        service = _make_service()
        identity_store_mock = cast(AsyncMock, service._identity_store)
        identity_store_mock.get_current = AsyncMock(
            return_value=None,
        )
        events = await service.evolve(agent_id=_AGENT_ID)
        assert events == ()
