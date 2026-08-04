"""Tests for the live capability probes.

The property under test is the one the design rests on: liveness is read
from what activation actually installed, never from a record kept
alongside it. The pipeline attachments and the initiative tail are where
that matters most, because those subsystems mutate an existing object in
place and install nothing else observable.
"""

from collections.abc import Callable

import pytest

from synthorg.api.state import AppState
from synthorg.api.subsystems.capabilities import CAPABILITIES
from synthorg.api.subsystems.spec import Capability, CapabilityId
from synthorg.engine.initiative.ports import (
    EvaluationPort,
    IntegrationPort,
    PlanStatusWriter,
    ReplanTriggerPort,
)
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.pipeline.narrator_port import RunNarrator
from synthorg.engine.pipeline.plan_review_panel_port import PlanReviewPanel
from synthorg.engine.pipeline.plan_review_port import PlanReviewGate
from synthorg.engine.pipeline.refinement_port import WorkRefinementRouter
from synthorg.engine.state import EngineStateSlice
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import FakeClock, StubWorkPipeline, make_app_state, mock_of

pytestmark = pytest.mark.unit

type Attach = Callable[[StubWorkPipeline], None]

_ATTACHMENT_CAPABILITIES: tuple[tuple[CapabilityId, Attach], ...] = (
    (
        CapabilityId.RUN_NARRATOR,
        lambda p: p.attach_narrator(mock_of[RunNarrator]()),
    ),
    (
        CapabilityId.REFINEMENT_ROUTER,
        lambda p: p.attach_refinement_router(mock_of[WorkRefinementRouter]()),
    ),
    (
        CapabilityId.PLAN_REVIEW_GATE,
        lambda p: p.attach_plan_review_gate(mock_of[PlanReviewGate]()),
    ),
    (
        CapabilityId.PLAN_REVIEW_PANEL,
        lambda p: p.attach_plan_review_panel(mock_of[PlanReviewPanel]()),
    ),
)


def _probe(cap_id: CapabilityId) -> Capability:
    for capability in CAPABILITIES:
        if capability.id is cap_id:
            return capability
    msg = f"no capability declared for {cap_id}"
    raise AssertionError(msg)


def _app_state() -> AppState:
    return make_app_state()


def _state_with_pipeline() -> tuple[AppState, StubWorkPipeline]:
    pipeline = StubWorkPipeline()
    return make_app_state(work_pipeline=pipeline), pipeline


@pytest.mark.parametrize(("cap_id", "attach"), _ATTACHMENT_CAPABILITIES)
def test_attaching_a_collaborator_makes_its_capability_present(
    cap_id: CapabilityId, attach: Attach
) -> None:
    """The probe flips only once the ``attach_*`` seam has actually run."""
    state, pipeline = _state_with_pipeline()
    probe = _probe(cap_id)

    assert probe.present(state) is False
    attach(pipeline)
    assert probe.present(state) is True


@pytest.mark.parametrize(("cap_id", "attach"), _ATTACHMENT_CAPABILITIES)
def test_an_attachment_capability_is_absent_before_the_pipeline_exists(
    cap_id: CapabilityId, attach: Attach
) -> None:
    """No pipeline means nothing is attached, not an exception mid-pass."""
    del attach
    assert _probe(cap_id).present(_app_state()) is False


def test_each_attachment_capability_is_independent() -> None:
    """Attaching one collaborator does not report the other three up."""
    state, pipeline = _state_with_pipeline()
    pipeline.attach_narrator(mock_of[RunNarrator]())

    others = [
        _probe(cap_id).present(state)
        for cap_id, _ in _ATTACHMENT_CAPABILITIES
        if cap_id is not CapabilityId.RUN_NARRATOR
    ]
    assert _probe(CapabilityId.RUN_NARRATOR).present(state) is True
    assert not any(others)


def _rollup_state(*, tail: bool) -> AppState:
    """An app state carrying a rollup, with or without its tail attached.

    Returns:
        The composed state, whose rollup is wired either way.
    """
    rollup = ProjectRollupService(
        persistence=mock_of[PersistenceBackend](),
        plan_status_writer=mock_of[PlanStatusWriter](),
        clock=FakeClock(),
    )
    if tail:
        rollup.attach_tail(
            replan_trigger=mock_of[ReplanTriggerPort](),
            integration=mock_of[IntegrationPort](),
            evaluation=lambda _trigger: mock_of[EvaluationPort](),
        )
    return make_app_state(
        slices={EngineStateSlice: {"project_rollup_service": rollup}},
    )


def test_a_rollup_without_its_tail_does_not_read_as_a_live_tail() -> None:
    """A wired rollup is not a wired tail, and reading it as one strands it.

    The rollup activates as soon as persistence and the task engine exist,
    which is before a provider is configured, so its first wire legitimately
    produces a rollup whose three tail stages all declined. If the tail's
    liveness is read from the rollup merely existing, the reconciler sees a
    converged subsystem and never revisits it, and the tail can never come up.
    """
    assert _probe(CapabilityId.INITIATIVE_TAIL).present(_rollup_state(tail=False)) is (
        False
    )


def test_attaching_the_tail_makes_its_capability_present() -> None:
    """The probe flips only once ``attach_tail`` has actually run."""
    assert (
        _probe(CapabilityId.INITIATIVE_TAIL).present(_rollup_state(tail=True)) is True
    )


def test_the_tail_capability_is_absent_before_the_rollup_exists() -> None:
    """No rollup means no tail, not an exception mid-pass."""
    assert _probe(CapabilityId.INITIATIVE_TAIL).present(_app_state()) is False


def test_every_declared_capability_probe_is_total() -> None:
    """A probe that raised would decide the fate of everything behind it."""
    state = _app_state()
    for capability in CAPABILITIES:
        assert isinstance(capability.present(state), bool)
