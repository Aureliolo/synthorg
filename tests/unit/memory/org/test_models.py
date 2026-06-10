"""Tests for org memory models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.hr.seniority import SeniorityLevel
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.org.models import (
    OperationLogEntry,
    OperationLogSnapshot,
    OrgFact,
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)
from tests._shared import as_uuid

_NOW = datetime.now(UTC)


@pytest.mark.unit
class TestOrgFactAuthor:
    """OrgFactAuthor validation and consistency."""

    def test_human_author(self) -> None:
        author = OrgFactAuthor(is_human=True)
        assert author.is_human is True
        assert author.agent_id is None
        assert author.seniority is None
        assert author.autonomy_level is None

    def test_agent_author(self) -> None:
        author = OrgFactAuthor(
            agent_id="agent-1",
            seniority=SeniorityLevel.SENIOR,
            is_human=False,
        )
        assert author.agent_id == "agent-1"
        assert author.seniority == SeniorityLevel.SENIOR
        assert author.autonomy_level is None

    def test_agent_author_with_autonomy(self) -> None:
        author = OrgFactAuthor(
            agent_id="agent-1",
            seniority=SeniorityLevel.SENIOR,
            autonomy_level=AutonomyLevel.SEMI,
            is_human=False,
        )
        assert author.autonomy_level == AutonomyLevel.SEMI

    def test_human_with_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Human authors must not"):
            OrgFactAuthor(is_human=True, agent_id="agent-1")

    def test_human_with_autonomy_level_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="Human authors must not have an autonomy level",
        ):
            OrgFactAuthor(
                is_human=True,
                autonomy_level=AutonomyLevel.FULL,
            )

    def test_agent_without_agent_id_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="Non-human authors must have an agent_id",
        ):
            OrgFactAuthor(is_human=False)

    def test_agent_without_seniority_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="Non-human authors must have a seniority",
        ):
            OrgFactAuthor(
                is_human=False,
                agent_id="agent-1",
                seniority=None,
            )

    def test_frozen(self) -> None:
        author = OrgFactAuthor(is_human=True)
        with pytest.raises(ValidationError):
            author.is_human = False  # type: ignore[misc]


@pytest.mark.unit
class TestOrgFact:
    """OrgFact creation and validation."""

    def test_valid_fact(self) -> None:
        author = OrgFactAuthor(is_human=True)
        fact = OrgFact(
            id=as_uuid("fact-1"),
            content="All code must be reviewed",
            category=OrgFactCategory.CORE_POLICY,
            author=author,
            created_at=_NOW,
        )
        assert fact.id == as_uuid("fact-1")
        assert fact.category == OrgFactCategory.CORE_POLICY
        assert fact.tags == ()

    def test_fact_with_tags(self) -> None:
        fact = OrgFact(
            id=as_uuid("fact-1"),
            content="Tagged fact",
            category=OrgFactCategory.ADR,
            tags=("core-policy", "security"),
            author=OrgFactAuthor(is_human=True),
            created_at=_NOW,
        )
        assert fact.tags == ("core-policy", "security")

    @pytest.mark.parametrize(
        "bad_tags",
        [("",), ("   ",), ("valid", "")],
        ids=["empty", "whitespace", "mixed"],
    )
    def test_fact_rejects_blank_tags(
        self,
        bad_tags: tuple[str, ...],
    ) -> None:
        with pytest.raises(ValidationError):
            OrgFact(
                id=as_uuid("fact-1"),
                content="test",
                category=OrgFactCategory.ADR,
                tags=bad_tags,
                author=OrgFactAuthor(is_human=True),
                created_at=_NOW,
            )

    @pytest.mark.parametrize(
        "bad_tags",
        [("",), ("   ",)],
        ids=["empty", "whitespace"],
    )
    def test_write_request_rejects_blank_tags(
        self,
        bad_tags: tuple[str, ...],
    ) -> None:
        with pytest.raises(ValidationError):
            OrgFactWriteRequest(
                content="test",
                category=OrgFactCategory.ADR,
                tags=bad_tags,
            )

    def test_frozen(self) -> None:
        fact = OrgFact(
            id=as_uuid("fact-1"),
            content="test",
            category=OrgFactCategory.ADR,
            author=OrgFactAuthor(is_human=True),
            created_at=_NOW,
        )
        with pytest.raises(ValidationError):
            fact.content = "modified"  # type: ignore[misc]


@pytest.mark.unit
class TestOrgFactWriteRequest:
    """OrgFactWriteRequest validation."""

    def test_valid_request(self) -> None:
        req = OrgFactWriteRequest(
            content="New convention",
            category=OrgFactCategory.CONVENTION,
        )
        assert req.content == "New convention"
        assert req.category == OrgFactCategory.CONVENTION
        assert req.tags == ()

    def test_request_with_tags(self) -> None:
        req = OrgFactWriteRequest(
            content="Tagged request",
            category=OrgFactCategory.ADR,
            tags=("important",),
        )
        assert req.tags == ("important",)

    def test_blank_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrgFactWriteRequest(content="", category=OrgFactCategory.ADR)

    def test_whitespace_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrgFactWriteRequest(
                content="   ",
                category=OrgFactCategory.ADR,
            )


@pytest.mark.unit
class TestOrgMemoryQuery:
    """OrgMemoryQuery validation."""

    def test_defaults(self) -> None:
        query = OrgMemoryQuery()
        assert query.context is None
        assert query.categories is None
        assert query.limit == 5

    def test_limit_bounds(self) -> None:
        assert OrgMemoryQuery(limit=1).limit == 1
        assert OrgMemoryQuery(limit=100).limit == 100
        with pytest.raises(ValidationError):
            OrgMemoryQuery(limit=0)
        with pytest.raises(ValidationError):
            OrgMemoryQuery(limit=101)

    def test_with_categories(self) -> None:
        query = OrgMemoryQuery(
            categories=frozenset(
                {OrgFactCategory.ADR, OrgFactCategory.PROCEDURE},
            ),
        )
        assert OrgFactCategory.ADR in query.categories  # type: ignore[operator]


@pytest.mark.unit
class TestOperationLogEntry:
    """OperationLogEntry validation."""

    def test_publish_entry(self) -> None:
        entry = OperationLogEntry(
            operation_id=as_uuid("op-1"),
            fact_id="fact-1",
            operation_type="PUBLISH",
            content="Test content",
            tags=("core-policy",),
            author_agent_id="agent-1",
            author_autonomy_level=AutonomyLevel.SEMI,
            timestamp=_NOW,
            version=1,
        )
        assert entry.operation_type == "PUBLISH"
        assert entry.content == "Test content"
        assert entry.version == 1

    def test_retract_entry_null_content(self) -> None:
        entry = OperationLogEntry(
            operation_id=as_uuid("op-2"),
            fact_id="fact-1",
            operation_type="RETRACT",
            content=None,
            timestamp=_NOW,
            version=2,
        )
        assert entry.operation_type == "RETRACT"
        assert entry.content is None

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            OperationLogEntry(
                operation_id=as_uuid("op-1"),
                fact_id="fact-1",
                operation_type="PUBLISH",
                content="test",
                timestamp=_NOW,
                version=0,
            )

    def test_publish_without_content_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="PUBLISH operations must have",
        ):
            OperationLogEntry(
                operation_id=as_uuid("op-1"),
                fact_id="fact-1",
                operation_type="PUBLISH",
                content=None,
                timestamp=_NOW,
                version=1,
            )

    def test_retract_with_content_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="RETRACT operations must have",
        ):
            OperationLogEntry(
                operation_id=as_uuid("op-1"),
                fact_id="fact-1",
                operation_type="RETRACT",
                content="should be None",
                timestamp=_NOW,
                version=1,
            )

    def test_frozen(self) -> None:
        entry = OperationLogEntry(
            operation_id=as_uuid("op-1"),
            fact_id="fact-1",
            operation_type="PUBLISH",
            content="test",
            timestamp=_NOW,
            version=1,
        )
        with pytest.raises(ValidationError):
            entry.version = 99  # type: ignore[misc]


@pytest.mark.unit
class TestOperationLogSnapshot:
    """OperationLogSnapshot validation."""

    def test_active_snapshot(self) -> None:
        snap = OperationLogSnapshot(
            fact_id="fact-1",
            content="Active fact",
            category=OrgFactCategory.ADR,
            tags=("tag-a",),
            created_at=_NOW,
            retracted_at=None,
            version=1,
        )
        assert snap.retracted_at is None
        assert snap.version == 1
        assert snap.category == OrgFactCategory.ADR

    def test_retracted_snapshot(self) -> None:
        snap = OperationLogSnapshot(
            fact_id="fact-1",
            content="Retracted fact",
            category=OrgFactCategory.CONVENTION,
            created_at=_NOW,
            retracted_at=_NOW,
            version=2,
        )
        assert snap.retracted_at is not None

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            OperationLogSnapshot(
                fact_id="fact-1",
                content="test",
                category=OrgFactCategory.ADR,
                created_at=_NOW,
                version=0,
            )

    def test_created_after_retracted_rejected(self) -> None:
        later = datetime(2026, 6, 1, tzinfo=UTC)
        earlier = datetime(2026, 1, 1, tzinfo=UTC)
        with pytest.raises(
            ValidationError,
            match="created_at must be <= retracted_at",
        ):
            OperationLogSnapshot(
                fact_id="fact-1",
                content="test",
                category=OrgFactCategory.ADR,
                created_at=later,
                retracted_at=earlier,
                version=1,
            )
