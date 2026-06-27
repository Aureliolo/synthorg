"""Conformance tests for ``AgentContributionRepository``."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.attribution import AgentContribution
from synthorg.persistence.agent_contribution_protocol import (
    AgentContributionFilterSpec,
)
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _contribution(
    *,
    agent_id: str = "agent-alpha",
    subtask_id: str = "subtask-1",
    score: float = 0.8,
) -> AgentContribution:
    return AgentContribution(
        agent_id=NotBlankStr(agent_id),
        subtask_id=NotBlankStr(subtask_id),
        contribution_score=score,
        failure_attribution="direct",
        evidence="merged two PRs",
    )


class TestAgentContributionRepository:
    async def test_append_and_query(self, backend: PersistenceBackend) -> None:
        await backend.agent_contributions.append(_contribution(score=0.5))
        await backend.agent_contributions.append(_contribution(score=0.9))

        results = await backend.agent_contributions.query(
            AgentContributionFilterSpec(agent_id=NotBlankStr("agent-alpha"))
        )
        assert len(results) == 2
        # Newest-first by insertion order: the 0.9 row was inserted last.
        assert results[0].contribution_score == pytest.approx(0.9)
        assert results[0].evidence == "merged two PRs"

    async def test_filter_by_subtask(self, backend: PersistenceBackend) -> None:
        await backend.agent_contributions.append(_contribution(subtask_id="st-x"))
        await backend.agent_contributions.append(_contribution(subtask_id="st-y"))

        only_x = await backend.agent_contributions.query(
            AgentContributionFilterSpec(subtask_id=NotBlankStr("st-x"))
        )
        assert all(c.subtask_id == "st-x" for c in only_x)
        assert len(only_x) == 1

    async def test_query_all(self, backend: PersistenceBackend) -> None:
        await backend.agent_contributions.append(_contribution(agent_id="ac-a"))
        await backend.agent_contributions.append(_contribution(agent_id="ac-b"))
        every = await backend.agent_contributions.query(AgentContributionFilterSpec())
        ids = {c.agent_id for c in every}
        assert {"ac-a", "ac-b"} <= ids

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        await backend.agent_contributions.append(_contribution())
        # All rows were recorded ~now; a far-past cutoff removes none.
        removed_none = await backend.agent_contributions.purge_before(
            datetime(2000, 1, 1, tzinfo=UTC)
        )
        assert removed_none == 0
        # A far-future cutoff removes everything appended so far.
        removed_all = await backend.agent_contributions.purge_before(
            datetime.now(UTC) + timedelta(days=1)
        )
        assert removed_all >= 1
        remaining = await backend.agent_contributions.query(
            AgentContributionFilterSpec(agent_id=NotBlankStr("agent-alpha"))
        )
        assert remaining == ()
