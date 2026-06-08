"""Tests for the steering supersession proposer."""

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.proposer import (
    LLMSupersessionProposer,
    NoOpSupersessionProposer,
    _parse_proposal,
    build_supersession_proposer,
)
from tests._shared import as_uuid, sid
from tests._shared.scripted_provider import ScriptedProvider, make_text_response


def _task(task_id: str) -> Task:
    return Task(
        id=as_uuid(task_id),
        title=f"Task {task_id}",
        description="A frontend task.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-001",
        created_by="pm",
        assigned_to="agent-1",
        status=TaskStatus.IN_PROGRESS,
    )


@pytest.mark.unit
class TestParseProposal:
    """JSON parsing restricts ids to the candidate set."""

    def test_parses_obsolete_ids(self) -> None:
        content = '{"obsolete_task_ids": ["t1", "t2"], "rationale": "obsolete"}'
        ids, rationale = _parse_proposal(content, {"t1", "t2", "t3"})
        assert set(ids) == {"t1", "t2"}
        assert rationale == "obsolete"

    def test_drops_unknown_ids(self) -> None:
        content = '{"obsolete_task_ids": ["t1", "ghost"]}'
        ids, _rationale = _parse_proposal(content, {"t1"})
        assert ids == ("t1",)

    def test_malformed_json_returns_empty(self) -> None:
        ids, rationale = _parse_proposal("not json at all", {"t1"})
        assert ids == ()
        assert rationale == ""


@pytest.mark.unit
class TestNoOpProposer:
    """The no-op proposer echoes the operator seed set."""

    async def test_echoes_seed(self) -> None:
        proposer = NoOpSupersessionProposer()
        proposal = await proposer.propose(
            directive_id=NotBlankStr("d1"),
            directive_text="use Postgres",
            candidate_tasks=(_task("t1"),),
            seed_task_ids=(NotBlankStr("t1"),),
        )
        assert proposal.proposed_task_ids == ("t1",)


@pytest.mark.unit
class TestLLMProposer:
    """The LLM proposer refines via the provider and is safe-by-default."""

    async def test_refines_from_provider(self) -> None:
        provider = ScriptedProvider(
            responses=[
                make_text_response(
                    f'{{"obsolete_task_ids": ["{sid("t1")}"], "rationale": "fe"}}'
                )
            ]
        )
        proposer = LLMSupersessionProposer(provider, model="test-model-001")
        proposal = await proposer.propose(
            directive_id=NotBlankStr("d1"),
            directive_text="pivot off the frontend",
            candidate_tasks=(_task("t1"), _task("t2")),
            seed_task_ids=(),
        )
        assert proposal.proposed_task_ids == (sid("t1"),)
        assert proposal.rationale == "fe"

    async def test_no_candidates_returns_seed(self) -> None:
        provider = ScriptedProvider(
            responses=[make_text_response('{"obsolete_task_ids": ["t9"]}')]
        )
        proposer = LLMSupersessionProposer(provider, model="test-model-001")
        proposal = await proposer.propose(
            directive_id=NotBlankStr("d1"),
            directive_text="x",
            candidate_tasks=(),
            seed_task_ids=(NotBlankStr("seed"),),
        )
        assert proposal.proposed_task_ids == ("seed",)


@pytest.mark.unit
class TestFactory:
    """The factory selects LLM vs no-op."""

    def test_no_provider_is_noop(self) -> None:
        proposer = build_supersession_proposer(None, model="m")
        assert isinstance(proposer, NoOpSupersessionProposer)

    def test_disabled_is_noop(self) -> None:
        proposer = build_supersession_proposer(
            ScriptedProvider(responses=[]),
            model="m",
            enabled=False,
        )
        assert isinstance(proposer, NoOpSupersessionProposer)

    def test_provider_and_model_is_llm(self) -> None:
        proposer = build_supersession_proposer(
            ScriptedProvider(responses=[]),
            model="m",
        )
        assert isinstance(proposer, LLMSupersessionProposer)
