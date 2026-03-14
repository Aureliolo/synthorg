"""Tests for org memory models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import OrgFactCategory, SeniorityLevel
from synthorg.memory.org.models import (
    OrgFact,
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)

pytestmark = pytest.mark.timeout(30)

_NOW = datetime.now(UTC)


@pytest.mark.unit
class TestOrgFactAuthor:
    """OrgFactAuthor validation and consistency."""

    def test_human_author(self) -> None:
        author = OrgFactAuthor(is_human=True)
        assert author.is_human is True
        assert author.agent_id is None
        assert author.seniority is None

    def test_agent_author(self) -> None:
        author = OrgFactAuthor(
            agent_id="agent-1",
            seniority=SeniorityLevel.SENIOR,
            is_human=False,
        )
        assert author.agent_id == "agent-1"
        assert author.seniority == SeniorityLevel.SENIOR

    def test_human_with_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Human authors must not"):
            OrgFactAuthor(is_human=True, agent_id="agent-1")

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
            id="fact-1",
            content="All code must be reviewed",
            category=OrgFactCategory.CORE_POLICY,
            author=author,
            created_at=_NOW,
            version=1,
        )
        assert fact.id == "fact-1"
        assert fact.category == OrgFactCategory.CORE_POLICY
        assert fact.version == 1

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            OrgFact(
                id="fact-1",
                content="test",
                category=OrgFactCategory.ADR,
                author=OrgFactAuthor(is_human=True),
                created_at=_NOW,
                version=0,
            )

    def test_frozen(self) -> None:
        fact = OrgFact(
            id="fact-1",
            content="test",
            category=OrgFactCategory.ADR,
            author=OrgFactAuthor(is_human=True),
            created_at=_NOW,
            version=1,
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

    def test_blank_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrgFactWriteRequest(content="", category=OrgFactCategory.ADR)

    def test_whitespace_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrgFactWriteRequest(content="   ", category=OrgFactCategory.ADR)


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
            categories=frozenset({OrgFactCategory.ADR, OrgFactCategory.PROCEDURE}),
        )
        assert OrgFactCategory.ADR in query.categories  # type: ignore[operator]
