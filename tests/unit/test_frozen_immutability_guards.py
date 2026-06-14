"""Deepcopy-at-construction guards on frozen models with mutable fields.

A frozen Pydantic model still holds a reference to whatever mutable
container the caller passed in; without a guard, the caller can mutate
that container after construction and silently change the "immutable"
model. Each model below carries an after-validator that deep-copies the
mutable field so the stored value is independent of the caller's
reference. These tests pin that behaviour: mutating the source container
after construction must not be visible through the model.
"""

from datetime import UTC, datetime

import pytest

from synthorg.communication.async_tasks.models import TaskSpec
from synthorg.core.completion_enums import FinishReason
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
    AdaptationSource,
)
from synthorg.engine.prompt_result import SystemPrompt
from synthorg.engine.review.models import ReviewStageResult, ReviewVerdict
from synthorg.hr.activity import ActivityEvent, CareerEvent
from synthorg.hr.enums import ActivityEventType, LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.infrastructure.services._registries import _ProjectRecord
from synthorg.meta.reports.models import Report
from synthorg.providers.models import ZERO_TOKEN_USAGE, CompletionResponse
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def test_review_stage_result_metadata_deepcopied() -> None:
    src: dict[str, object] = {"nested": [1]}
    result = ReviewStageResult(
        stage_name="lint",
        verdict=ReviewVerdict.PASS,
        metadata=src,
    )
    src["nested"] = [1, 2]
    assert result.metadata == {"nested": [1]}


def test_completion_response_provider_metadata_deepcopied() -> None:
    src: dict[str, object] = {"latency": {"ms": 10}}
    response = CompletionResponse(
        content="hi",
        finish_reason=FinishReason.STOP,
        usage=ZERO_TOKEN_USAGE,
        model="example-medium-001",
        provider_metadata=src,
    )
    src["latency"] = {"ms": 999}
    assert response.provider_metadata == {"latency": {"ms": 10}}


def test_system_prompt_metadata_deepcopied() -> None:
    src = {"agent_id": "a1"}
    prompt = SystemPrompt(
        content="You are an agent.",
        template_version="1.0",
        estimated_tokens=0,
        sections=("core",),
        metadata=src,
    )
    src["agent_id"] = "tampered"
    assert prompt.metadata == {"agent_id": "a1"}


def test_agent_lifecycle_event_metadata_deepcopied() -> None:
    src = {"reason": "onboarding"}
    event = AgentLifecycleEvent(
        agent_id="agent-1",
        agent_name="Alice",
        event_type=LifecycleEventType.HIRED,
        timestamp=datetime.now(UTC),
        initiated_by="system",
        metadata=src,
    )
    src["reason"] = "tampered"
    assert event.metadata == {"reason": "onboarding"}


def test_task_spec_metadata_deepcopied() -> None:
    src = {"priority": "high"}
    spec = TaskSpec(
        title="Investigate",
        description="Look into the flake",
        agent_id="agent-1",
        metadata=src,
    )
    src["priority"] = "low"
    assert spec.metadata == {"priority": "high"}


def test_activity_event_related_ids_deepcopied() -> None:
    src = {"task_id": "t-1"}
    event = ActivityEvent(
        event_type=ActivityEventType.HIRED,
        timestamp=datetime.now(UTC),
        related_ids=src,
    )
    src["task_id"] = "tampered"
    assert event.related_ids == {"task_id": "t-1"}


def test_career_event_metadata_deepcopied() -> None:
    src = {"promotion": "senior"}
    event = CareerEvent(
        event_type=LifecycleEventType.HIRED,
        timestamp=datetime.now(UTC),
        initiated_by="system",
        metadata=src,
    )
    src["promotion"] = "tampered"
    assert event.metadata == {"promotion": "senior"}


def test_report_content_and_options_deepcopied() -> None:
    content: dict[str, object] = {"sections": [{"k": "v"}]}
    options = {"format": "pdf"}
    report = Report(
        template="summary",
        title="My Report",
        author_id="op-1",
        content=content,
        options=options,
    )
    content["sections"] = ["tampered"]
    options["format"] = "tampered"
    assert report.content == {"sections": [{"k": "v"}]}
    assert report.options == {"format": "pdf"}


def test_adaptation_proposal_changes_deepcopied() -> None:
    src: dict[str, object] = {"prompt": {"tone": "warm"}}
    proposal = AdaptationProposal(
        agent_id="agent-1",
        axis=AdaptationAxis.IDENTITY,
        description="Adjust tone",
        confidence=0.9,
        source=AdaptationSource.FAILURE,
        changes=src,
    )
    src["prompt"] = {"tone": "tampered"}
    assert proposal.changes == {"prompt": {"tone": "warm"}}


def test_project_record_metadata_is_isolated_and_read_only() -> None:
    src = {"team": "platform"}
    record = _ProjectRecord(
        id=as_uuid("proj"),
        name="proj",
        description="",
        created_at=datetime.now(UTC),
        metadata=src,
    )
    src["team"] = "tampered"
    assert record.metadata["team"] == "platform"
    with pytest.raises(TypeError):
        record.metadata["team"] = "blocked"  # type: ignore[index]
