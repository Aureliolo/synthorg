"""Conformance tests for the generic ``VersionRepository`` protocol.

Exercises the generic machinery via ``backend.workflow_versions`` since
that is the canonical concrete instantiation. If the generic layer works
for one entity, it works for all of them.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import BaseModel

from synthorg.budget.config import BudgetConfig
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.company import Company
from synthorg.core.role import Role
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import WorkflowDefinition, WorkflowNode
from synthorg.engine.workflow.enums import WorkflowNodeType, WorkflowType
from synthorg.hr.evaluation.config import EvaluationConfig
from synthorg.hr.seniority import SeniorityLevel
from synthorg.organization.enums import DepartmentName
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.versioning.hashing import compute_content_hash
from synthorg.versioning.models import VersionSnapshot
from tests._shared import as_pk, as_uuid, sid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)


_START_NODE = WorkflowNode(
    id=NotBlankStr("start"),
    type=WorkflowNodeType.START,
    label=NotBlankStr("Start"),
)
_END_NODE = WorkflowNode(
    id=NotBlankStr("end"),
    type=WorkflowNodeType.END,
    label=NotBlankStr("End"),
)


def _definition(
    definition_id: str = "wf-001",
    revision: int = 1,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=as_pk(definition_id),
        name=NotBlankStr("Example"),
        description="",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version=NotBlankStr("1.0.0"),
        nodes=(_START_NODE, _END_NODE),
        edges=(),
        created_by=NotBlankStr("alice"),
        created_at=_NOW,
        updated_at=_NOW,
        revision=revision,
    )


def _snapshot(
    entity_id: str = "wf-001",
    version: int = 1,
    definition: WorkflowDefinition | None = None,
    saved_at: datetime = _NOW,
) -> VersionSnapshot[WorkflowDefinition]:
    d = definition or _definition(definition_id=entity_id, revision=version)
    return VersionSnapshot(
        entity_id=sid(entity_id),
        version=version,
        content_hash=NotBlankStr(compute_content_hash(d)),
        snapshot=d,
        saved_by=NotBlankStr("alice"),
        saved_at=saved_at,
    )


class TestVersionRepository:
    async def test_save_and_get_version(self, backend: PersistenceBackend) -> None:
        inserted = await backend.workflow_versions.save_version(_snapshot())
        assert inserted is True

        fetched = await backend.workflow_versions.get_version(
            sid("wf-001"),
            1,
        )
        assert fetched is not None
        assert fetched.version == 1
        assert fetched.snapshot.id == as_uuid("wf-001")

    async def test_save_version_is_idempotent(
        self, backend: PersistenceBackend
    ) -> None:
        snap = _snapshot()
        assert await backend.workflow_versions.save_version(snap) is True
        assert await backend.workflow_versions.save_version(snap) is False

    async def test_get_version_missing(self, backend: PersistenceBackend) -> None:
        fetched = await backend.workflow_versions.get_version(
            NotBlankStr("ghost"),
            1,
        )
        assert fetched is None

    async def test_get_latest_version(self, backend: PersistenceBackend) -> None:
        await backend.workflow_versions.save_version(
            _snapshot(version=1, saved_at=_NOW),
        )
        await backend.workflow_versions.save_version(
            _snapshot(
                version=2,
                definition=_definition(revision=2),
                saved_at=_NOW + timedelta(minutes=1),
            ),
        )
        await backend.workflow_versions.save_version(
            _snapshot(
                version=3,
                definition=_definition(revision=3),
                saved_at=_NOW + timedelta(minutes=2),
            ),
        )

        latest = await backend.workflow_versions.get_latest_version(
            sid("wf-001"),
        )
        assert latest is not None
        assert latest.version == 3

    async def test_get_by_content_hash(self, backend: PersistenceBackend) -> None:
        d = _definition()
        hash_ = compute_content_hash(d)
        await backend.workflow_versions.save_version(_snapshot(definition=d))

        fetched = await backend.workflow_versions.get_by_content_hash(
            sid("wf-001"),
            NotBlankStr(hash_),
        )
        assert fetched is not None
        assert fetched.content_hash == hash_

    async def test_list_versions_descending(self, backend: PersistenceBackend) -> None:
        await backend.workflow_versions.save_version(_snapshot(version=1))
        await backend.workflow_versions.save_version(
            _snapshot(version=2, definition=_definition(revision=2)),
        )

        rows = await backend.workflow_versions.list_versions(sid("wf-001"))
        versions = [r.version for r in rows]
        assert versions == [2, 1]

    async def test_count_versions(self, backend: PersistenceBackend) -> None:
        await backend.workflow_versions.save_version(_snapshot(version=1))
        await backend.workflow_versions.save_version(
            _snapshot(version=2, definition=_definition(revision=2)),
        )

        count = await backend.workflow_versions.count_versions(sid("wf-001"))
        assert count == 2

    async def test_delete_versions_for_entity(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.workflow_versions.save_version(_snapshot(version=1))
        await backend.workflow_versions.save_version(
            _snapshot(version=2, definition=_definition(revision=2)),
        )

        removed = await backend.workflow_versions.delete_versions_for_entity(
            sid("wf-001"),
        )
        assert removed == 2
        assert await backend.workflow_versions.get_latest_version(sid("wf-001")) is None


def _versioned_snap[T: BaseModel](
    entity_id: str, model: T, *, version: int = 1
) -> VersionSnapshot[T]:
    return VersionSnapshot(
        entity_id=sid(entity_id),
        version=version,
        content_hash=NotBlankStr(compute_content_hash(model)),
        snapshot=model,
        saved_by=NotBlankStr("alice"),
        saved_at=_NOW,
    )


class TestVersionRepositoryTypedAccessors:
    """Round-trip each typed ``VersionRepository`` accessor on both backends.

    The generic machinery is covered by ``TestVersionRepository`` via
    ``workflow_versions``; these tests pin the per-accessor table binding and
    the entity-specific ``deserialize_snapshot`` callable for the remaining
    five typed instantiations, which a single-instantiation suite cannot catch
    (a missing migration, a wrong table-name argument, or a ``model_validate``
    failure would be invisible otherwise).
    """

    async def test_identity_versions_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        identity = AgentIdentity(
            name=NotBlankStr("Ada"),
            role=NotBlankStr("Engineer"),
            department=NotBlankStr("Engineering"),
            model=ModelConfig(
                provider=NotBlankStr("test-provider"),
                model_id=NotBlankStr("test-model-001"),
            ),
            hiring_date=date(2026, 1, 1),
        )
        assert (
            await backend.identity_versions.save_version(
                _versioned_snap("agent-1", identity)
            )
            is True
        )
        fetched = await backend.identity_versions.get_version(sid("agent-1"), 1)
        assert fetched is not None
        assert fetched.snapshot.name == "Ada"

    async def test_evaluation_config_versions_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        config = EvaluationConfig()
        assert (
            await backend.evaluation_config_versions.save_version(
                _versioned_snap("default", config)
            )
            is True
        )
        fetched = await backend.evaluation_config_versions.get_version(
            sid("default"), 1
        )
        assert fetched is not None
        assert fetched.snapshot == config

    async def test_budget_config_versions_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        config = BudgetConfig(total_monthly=100.0)
        assert (
            await backend.budget_config_versions.save_version(
                _versioned_snap("default", config)
            )
            is True
        )
        fetched = await backend.budget_config_versions.get_version(sid("default"), 1)
        assert fetched is not None
        assert fetched.snapshot.total_monthly == 100.0

    async def test_company_versions_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        company = Company(name=NotBlankStr("Acme"))
        assert (
            await backend.company_versions.save_version(
                _versioned_snap("company-1", company)
            )
            is True
        )
        fetched = await backend.company_versions.get_version(sid("company-1"), 1)
        assert fetched is not None
        assert fetched.snapshot.name == "Acme"

    async def test_role_versions_round_trip(self, backend: PersistenceBackend) -> None:
        role = Role(
            name=NotBlankStr("Backend Developer"),
            department=DepartmentName.ENGINEERING,
            required_skills=(NotBlankStr("python"),),
            authority_level=SeniorityLevel.SENIOR,
            tool_access=(NotBlankStr("git"),),
            description="Backend role.",
        )
        assert (
            await backend.role_versions.save_version(
                _versioned_snap("Backend Developer", role)
            )
            is True
        )
        fetched = await backend.role_versions.get_version(sid("Backend Developer"), 1)
        assert fetched is not None
        assert fetched.snapshot.name == "Backend Developer"
