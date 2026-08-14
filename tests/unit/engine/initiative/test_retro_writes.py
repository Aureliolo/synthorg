# module-kind: tests
"""Unit tests for retrospective material assembly, idempotency, and writes."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.retro_models import (
    AgentLearning,
    OrgLearning,
    RetrospectiveDraft,
    retro_object_tag,
)
from synthorg.engine.initiative.retro_writes import (
    already_captured,
    build_retro_material,
    write_learnings,
)
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.org.errors import OrgMemoryAccessDeniedError
from synthorg.memory.org.models import OrgFact, OrgFactAuthor
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

_LEAD_ID = as_uuid("lead")
_MEMBER_ID = sid("member")
_PROJECT_ID = "proj-1"


def _lead() -> AgentIdentity:
    return AgentIdentity(
        id=_LEAD_ID,
        name="Lead",
        role="Engineering Manager",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
        hiring_date=date(2026, 1, 1),
    )


def _project() -> Project:
    return Project(
        id=as_uuid(_PROJECT_ID),
        name=NotBlankStr("Checkout Hardening"),
        team=(NotBlankStr(str(_LEAD_ID)), NotBlankStr(_MEMBER_ID)),
        lead=NotBlankStr(str(_LEAD_ID)),
    )


def _plan() -> Plan:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    return Plan(
        id=as_uuid("plan-1"),
        project=NotBlankStr(sid(_PROJECT_ID)),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Harden checkout"),
        parent_task_id=NotBlankStr(sid("parent")),
        objective_criteria=(NotBlankStr("no dropped orders"),),
        items=(
            PlanItem(
                id=NotBlankStr(sid("i1")),
                title=NotBlankStr("Add retries"),
                description=NotBlankStr("retry logic"),
                acceptance_criteria=(NotBlankStr("retries covered"),),
                expected_artifacts=(NotBlankStr("src/retries.py"),),
                kind=PlanItemKind.WORK,
            ),
        ),
        status=PlanStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )


class TestBuildRetroMaterial:
    def test_includes_objective_criteria_and_items(self) -> None:
        material = build_retro_material(_plan(), _project())
        assert "Harden checkout" in material
        assert "no dropped orders" in material
        assert "Add retries" in material
        assert "Checkout Hardening" in material


class TestAlreadyCaptured:
    async def test_true_when_a_fact_carries_the_objective_tag(self) -> None:
        tag = retro_object_tag(_PROJECT_ID)
        fact = OrgFact(
            content=NotBlankStr("Prefer idempotent retries."),
            category=OrgFactCategory.CONVENTION,
            tags=(tag,),
            author=OrgFactAuthor(is_human=True),
            created_at=datetime(2026, 7, 19, tzinfo=UTC),
        )
        org = mock_of[OrgMemoryBackend](query=AsyncMock(return_value=(fact,)))

        assert await already_captured(org, project_id=_PROJECT_ID) is True

    async def test_false_when_no_fact_carries_the_tag(self) -> None:
        org = mock_of[OrgMemoryBackend](query=AsyncMock(return_value=()))
        assert await already_captured(org, project_id=_PROJECT_ID) is False


class TestWriteLearnings:
    async def test_writes_org_and_agent_learnings(self) -> None:
        org = mock_of[OrgMemoryBackend](write=AsyncMock(return_value=NotBlankStr("f")))
        mem = mock_of[MemoryBackend](store=AsyncMock(return_value=NotBlankStr("m")))
        draft = RetrospectiveDraft(
            summary=NotBlankStr("Went well."),
            org_learnings=(
                OrgLearning(
                    content=NotBlankStr("Retry idempotently."), kind="convention"
                ),
            ),
            agent_learnings=(
                AgentLearning(
                    agent_id=NotBlankStr(_MEMBER_ID), content=NotBlankStr("x")
                ),
            ),
        )

        project = _project()
        result = await write_learnings(
            draft,
            lead=_lead(),
            project=project,
            memory_backend=mem,
            org_backend=org,
        )

        assert result.org_written == 1
        assert result.agent_written == 1
        # The org write carries the convention category and the lead as author.
        org_request = org.write.await_args.args[0]
        assert org_request.category is OrgFactCategory.CONVENTION
        assert retro_object_tag(str(project.id)) in org_request.tags
        assert org.write.await_args.kwargs["author"].agent_id == str(_LEAD_ID)

    async def test_a_refused_org_write_does_not_lose_the_rest(self) -> None:
        org = mock_of[OrgMemoryBackend](
            write=AsyncMock(
                side_effect=[
                    OrgMemoryAccessDeniedError("not capable"),
                    NotBlankStr("f2"),
                ]
            )
        )
        mem = mock_of[MemoryBackend](store=AsyncMock(return_value=NotBlankStr("m")))
        draft = RetrospectiveDraft(
            summary=NotBlankStr("ok"),
            org_learnings=(
                OrgLearning(content=NotBlankStr("first"), kind="procedure"),
                OrgLearning(content=NotBlankStr("second"), kind="convention"),
            ),
        )

        result = await write_learnings(
            draft,
            lead=_lead(),
            project=_project(),
            memory_backend=mem,
            org_backend=org,
        )

        assert result.org_written == 1

    async def test_skips_a_learning_for_an_agent_not_on_the_initiative(self) -> None:
        org = mock_of[OrgMemoryBackend](write=AsyncMock(return_value=NotBlankStr("f")))
        mem = mock_of[MemoryBackend](store=AsyncMock(return_value=NotBlankStr("m")))
        draft = RetrospectiveDraft(
            summary=NotBlankStr("ok"),
            agent_learnings=(
                AgentLearning(
                    agent_id=NotBlankStr(sid("stranger")), content=NotBlankStr("x")
                ),
            ),
        )

        result = await write_learnings(
            draft,
            lead=_lead(),
            project=_project(),
            memory_backend=mem,
            org_backend=org,
        )

        assert result.agent_written == 0
        mem.store.assert_not_awaited()
