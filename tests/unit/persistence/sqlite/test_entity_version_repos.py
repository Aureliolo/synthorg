"""Tests for entity-specific version repository instantiations.

Validates that WorkflowDefinition, BudgetConfig, EvaluationConfig,
Company, and Role models survive JSON round-trip serialization through
the generic SQLiteVersionRepository.
"""

import json
from datetime import UTC, datetime

import aiosqlite
import pytest
from pydantic import BaseModel

from synthorg.persistence.sqlite.version_repo import SQLiteVersionRepository
from synthorg.versioning.hashing import compute_content_hash
from synthorg.versioning.models import VersionSnapshot
from tests._shared.persistence import make_private_write_context

_NOW = datetime(2026, 4, 8, 12, 0, tzinfo=UTC)

_CREATE_TABLE_TPL = """
CREATE TABLE IF NOT EXISTS {table} (
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
    PRIMARY KEY (entity_id, version)
)
"""


def _make_snapshot[T: BaseModel](
    entity_id: str,
    model: T,
    version: int = 1,
) -> VersionSnapshot[T]:
    return VersionSnapshot(
        entity_id=entity_id,
        version=version,
        content_hash=compute_content_hash(model),
        snapshot=model,
        saved_by="test-user",
        saved_at=_NOW,
    )


async def _make_repo[T: BaseModel](
    db: aiosqlite.Connection,
    table: str,
    model_cls: type[T],
) -> SQLiteVersionRepository[T]:
    db.row_factory = aiosqlite.Row
    await db.execute(_CREATE_TABLE_TPL.format(table=table))
    return SQLiteVersionRepository(
        db,
        table_name=table,
        serialize_snapshot=lambda m: json.dumps(
            m.model_dump(mode="json"),
        ),
        deserialize_snapshot=lambda s: model_cls.model_validate(
            json.loads(s),
        ),
        write_context=make_private_write_context(),
    )


# ── WorkflowDefinition round-trip ───────────────────────────────


@pytest.mark.unit
async def test_workflow_definition_roundtrip() -> None:
    """WorkflowDefinition with nodes/edges survives serialization."""
    from synthorg.engine.workflow.definition import (
        WorkflowDefinition,
        WorkflowEdge,
        WorkflowNode,
    )
    from synthorg.engine.workflow.enums import WorkflowNodeType, WorkflowType

    defn = WorkflowDefinition(
        id="wfdef-roundtrip",
        name="Test Workflow",
        description="A test workflow",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        nodes=(
            WorkflowNode(
                id="start",
                type=WorkflowNodeType.START,
                label="Start",
            ),
            WorkflowNode(
                id="end",
                type=WorkflowNodeType.END,
                label="End",
                position_x=200.0,
            ),
        ),
        edges=(
            WorkflowEdge(
                id="e1",
                source_node_id="start",
                target_node_id="end",
            ),
        ),
        created_by="test-user",
    )

    async with aiosqlite.connect(":memory:") as db:
        repo = await _make_repo(
            db,
            "wf_versions",
            WorkflowDefinition,
        )
        snap = _make_snapshot("wfdef-roundtrip", defn)
        inserted = await repo.save_version(snap)
        assert inserted is True

        loaded = await repo.get_version("wfdef-roundtrip", 1)
        assert loaded is not None
        assert loaded.snapshot.name == "Test Workflow"
        assert len(loaded.snapshot.nodes) == 2
        assert len(loaded.snapshot.edges) == 1
        assert loaded.snapshot.nodes[0].id == "start"
        assert loaded.content_hash == snap.content_hash


# ── BudgetConfig round-trip ─────────────────────────────────────


@pytest.mark.unit
async def test_budget_config_roundtrip() -> None:
    """BudgetConfig survives serialization with defaults."""
    from synthorg.budget.config import BudgetConfig

    config = BudgetConfig()

    async with aiosqlite.connect(":memory:") as db:
        repo = await _make_repo(
            db,
            "budget_versions",
            BudgetConfig,
        )
        snap = _make_snapshot("default", config)
        inserted = await repo.save_version(snap)
        assert inserted is True

        loaded = await repo.get_version("default", 1)
        assert loaded is not None
        assert loaded.snapshot.total_monthly == config.total_monthly
        assert loaded.snapshot.currency == config.currency


# ── EvaluationConfig round-trip ─────────────────────────────────


@pytest.mark.unit
async def test_evaluation_config_roundtrip() -> None:
    """EvaluationConfig survives serialization with defaults."""
    from synthorg.hr.evaluation.config import EvaluationConfig

    config = EvaluationConfig()

    async with aiosqlite.connect(":memory:") as db:
        repo = await _make_repo(
            db,
            "eval_versions",
            EvaluationConfig,
        )
        snap = _make_snapshot("default", config)
        inserted = await repo.save_version(snap)
        assert inserted is True

        loaded = await repo.get_version("default", 1)
        assert loaded is not None
        assert (
            loaded.snapshot.calibration_drift_threshold
            == config.calibration_drift_threshold
        )


# ── Company round-trip ─────────────────────────────────────────


@pytest.mark.unit
async def test_company_roundtrip() -> None:
    """Company survives serialization through generic repo."""
    from synthorg.core.company import Company

    company = Company(name="Test Corp")

    async with aiosqlite.connect(":memory:") as db:
        repo = await _make_repo(db, "company_versions", Company)
        snap = _make_snapshot("default", company)
        inserted = await repo.save_version(snap)
        assert inserted is True

        loaded = await repo.get_version("default", 1)
        assert loaded is not None
        assert loaded.snapshot.name == "Test Corp"


# ── Duplicate PK idempotency ──────────────────────────────────


@pytest.mark.unit
async def test_duplicate_pk_idempotency() -> None:
    """INSERT OR IGNORE rejects duplicate (entity_id, version) pairs."""
    from synthorg.budget.config import BudgetConfig

    config = BudgetConfig()

    async with aiosqlite.connect(":memory:") as db:
        repo = await _make_repo(
            db,
            "budget_versions",
            BudgetConfig,
        )
        snap1 = _make_snapshot("default", config, version=1)
        assert await repo.save_version(snap1) is True

        # Same content, different version number -- succeeds.
        snap2 = _make_snapshot("default", config, version=2)
        assert await repo.save_version(snap2) is True

        # Same version number (duplicate PK) -- rejected.
        assert await repo.save_version(snap1) is False

        count = await repo.count_versions("default")
        assert count == 2


# ── Role round-trip ─────────────────────────────────────────────


@pytest.mark.unit
async def test_role_roundtrip() -> None:
    """Role survives serialization through generic repo."""
    from synthorg.core.role import Role
    from synthorg.organization.enums import DepartmentName

    role = Role(
        name="backend-developer",
        department=DepartmentName.ENGINEERING,
        required_skills=(),
        system_prompt_template="You are a backend developer.",
    )

    async with aiosqlite.connect(":memory:") as db:
        repo = await _make_repo(db, "role_versions", Role)
        snap = _make_snapshot("backend-developer", role)
        inserted = await repo.save_version(snap)
        assert inserted is True

        loaded = await repo.get_version("backend-developer", 1)
        assert loaded is not None
        assert loaded.snapshot.name == "backend-developer"
        assert loaded.snapshot.department == DepartmentName.ENGINEERING
