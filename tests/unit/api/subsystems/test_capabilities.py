"""Tests for the live capability probes.

The property under test is the one the design rests on: liveness is read
from what activation actually installed, never from a record kept
alongside it. The pipeline attachments and the initiative tail are where
that matters most, because those subsystems mutate an existing object in
place and install nothing else observable.
"""

from collections.abc import Callable
from typing import Final

import pytest

from synthorg.api.state import AppState
from synthorg.api.subsystems.capabilities import CAPABILITIES
from synthorg.api.subsystems.spec import Capability, CapabilityId
from synthorg.engine.initiative.ports import (
    EvaluationPort,
    IntegrationPort,
    PlanStatusWriter,
    ReplanTriggerPort,
    RetroCapturePort,
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


_TAIL_CAPABILITIES: Final[tuple[CapabilityId, ...]] = (
    CapabilityId.INITIATIVE_INTEGRATE,
    CapabilityId.INITIATIVE_EVALUATE,
    CapabilityId.INITIATIVE_REPLAN,
    CapabilityId.INITIATIVE_RETRO_CAPTURE,
)


def _rollup_state(**attached: object) -> AppState:
    """An app state carrying a rollup with *attached* tail collaborators.

    Returns:
        The composed state, whose rollup is wired either way.
    """
    rollup = ProjectRollupService(
        persistence=mock_of[PersistenceBackend](),
        plan_status_writer=mock_of[PlanStatusWriter](),
        clock=FakeClock(),
    )
    if attached:
        rollup.attach_tail(**attached)  # type: ignore[arg-type]
    return make_app_state(
        slices={EngineStateSlice: {"project_rollup_service": rollup}},
    )


def test_a_rollup_without_its_tail_does_not_read_as_a_live_tail() -> None:
    """A wired rollup is not a wired tail, and reading it as one strands it.

    The rollup activates as soon as persistence and the task engine exist,
    which is before a provider is configured, so its first wire legitimately
    produces a rollup whose tail collaborators all declined. If their liveness
    is read from the rollup merely existing, the reconciler sees converged
    subsystems and never revisits them, and the tail can never come up.
    """
    state = _rollup_state()
    assert not any(_probe(c).present(state) for c in _TAIL_CAPABILITIES)


def test_each_tail_collaborator_is_probed_on_its_own() -> None:
    """One attached collaborator must not make the other three read as live.

    They resolve independently: the retro capture needs both memory backends
    while the stages need registries and the coordinator, so a shared probe
    would let a boot with memory blocked report a capture that never fires.
    """
    for attached, capability in (
        (
            {"integration": mock_of[IntegrationPort]()},
            CapabilityId.INITIATIVE_INTEGRATE,
        ),
        (
            {"evaluation": mock_of[EvaluationPort]()},
            CapabilityId.INITIATIVE_EVALUATE,
        ),
        (
            {"replan_trigger": mock_of[ReplanTriggerPort]()},
            CapabilityId.INITIATIVE_REPLAN,
        ),
        (
            {"ship_retro_capture": mock_of[RetroCapturePort]()},
            CapabilityId.INITIATIVE_RETRO_CAPTURE,
        ),
    ):
        state = _rollup_state(**attached)
        present = {c for c in _TAIL_CAPABILITIES if _probe(c).present(state)}
        assert present == {capability}


def test_the_tail_capabilities_are_absent_before_the_rollup_exists() -> None:
    """No rollup means no tail, not an exception mid-pass."""
    state = _app_state()
    assert not any(_probe(c).present(state) for c in _TAIL_CAPABILITIES)


def test_every_declared_capability_probe_is_total() -> None:
    """A probe that raised would decide the fate of everything behind it."""
    state = _app_state()
    for capability in CAPABILITIES:
        assert isinstance(capability.present(state), bool)
