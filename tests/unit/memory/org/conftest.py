"""Shared pytest fixtures and helpers for org memory tests."""

from datetime import UTC, datetime

from synthorg.core.enums import AutonomyLevel, OrgFactCategory, SeniorityLevel
from synthorg.memory.org.models import OrgFact, OrgFactAuthor
from synthorg.persistence.sqlite.org_fact_repo import SQLiteOrgFactRepository

_NOW = datetime.now(UTC)
HUMAN_AUTHOR = OrgFactAuthor(is_human=True)
AGENT_AUTHOR = OrgFactAuthor(
    agent_id="agent-1",
    seniority=SeniorityLevel.SENIOR,
    autonomy_level=AutonomyLevel.SEMI,
    is_human=False,
)


def _make_fact(
    fact_id: str = "fact-1",
    content: str = "Test fact",
    category: OrgFactCategory = OrgFactCategory.ADR,
    *,
    author: OrgFactAuthor = HUMAN_AUTHOR,
    tags: tuple[str, ...] = (),
) -> OrgFact:
    """Create a test OrgFact with sensible defaults."""
    return OrgFact(
        id=fact_id,
        content=content,
        category=category,
        tags=tags,
        author=author,
        created_at=_NOW,
    )


# Minimal DDL for unit-test fixtures.  Real deployments run yoyo
# migrations; these tests only need the two tables the repository
# touches so fixtures stay self-contained.
_OP_LOG_DDL = """
CREATE TABLE IF NOT EXISTS org_facts_operation_log (
    operation_id TEXT PRIMARY KEY,
    fact_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK(operation_type IN ('PUBLISH', 'RETRACT')),
    content TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    author_agent_id TEXT,
    author_seniority TEXT,
    author_is_human INTEGER NOT NULL DEFAULT 0,
    author_autonomy_level TEXT,
    category TEXT,
    timestamp TEXT NOT NULL,
    version INTEGER NOT NULL,
    UNIQUE(fact_id, version)
)
"""
_SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS org_facts_snapshot (
    fact_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    author_agent_id TEXT,
    author_seniority TEXT,
    author_is_human INTEGER NOT NULL DEFAULT 0,
    author_autonomy_level TEXT,
    created_at TEXT NOT NULL,
    retracted_at TEXT,
    version INTEGER NOT NULL
)
"""


# Keep the old export name so tests that aliased it don't break.
SQLiteOrgFactStore = SQLiteOrgFactRepository
