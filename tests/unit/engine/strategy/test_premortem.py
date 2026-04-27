"""Unit tests for premortem analysis."""

import pytest
from pydantic import ValidationError

from synthorg.communication.meeting.models import AgentResponse
from synthorg.engine.strategy.models import (
    PremortemConfig,
    PremortemParticipation,
)
from synthorg.engine.strategy.premortem import (
    DefaultPremortemExecutor,
    FailureMode,
    PremortemExecutor,
    PremortemOutput,
)


class TestFailureMode:
    """Tests for FailureMode model."""

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """FailureMode should be frozen."""
        mode = FailureMode(description="System overload")
        with pytest.raises(ValidationError):
            mode.description = "Different"  # type: ignore[misc]

    @pytest.mark.unit
    def test_defaults(self) -> None:
        """Default values for likelihood, impact, mitigation."""
        mode = FailureMode(description="Something fails")
        assert mode.likelihood == "medium"
        assert mode.impact == "medium"
        assert mode.mitigation == "No mitigation identified"

    @pytest.mark.unit
    def test_valid_likelihood_values(self) -> None:
        """likelihood must be low/medium/high."""
        for likelihood in ["low", "medium", "high"]:
            mode = FailureMode(
                description="Test",
                likelihood=likelihood,  # type: ignore[arg-type]
            )
            assert mode.likelihood == likelihood

        with pytest.raises(ValidationError):
            FailureMode(description="Test", likelihood="very_high")  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_valid_impact_values(self) -> None:
        """impact must be low/medium/high."""
        for impact in ["low", "medium", "high"]:
            mode = FailureMode(
                description="Test",
                impact=impact,  # type: ignore[arg-type]
            )
            assert mode.impact == impact

        with pytest.raises(ValidationError):
            FailureMode(description="Test", impact="severe")  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_custom_mitigation(self) -> None:
        """Custom mitigation can be provided."""
        mode = FailureMode(
            description="Database failure",
            mitigation="Implement connection pooling",
        )
        assert mode.mitigation == "Implement connection pooling"


class TestPremortemOutput:
    """Tests for PremortemOutput model."""

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """PremortemOutput should be frozen."""
        output = PremortemOutput()
        with pytest.raises(ValidationError):
            output.failure_modes = ()  # type: ignore[misc]

    @pytest.mark.unit
    def test_empty_output(self) -> None:
        """Empty output is valid."""
        output = PremortemOutput()
        assert output.failure_modes == ()
        assert output.assumptions == ()

    @pytest.mark.unit
    def test_with_failure_modes(self) -> None:
        """Output can contain failure modes."""
        modes = (
            FailureMode(description="Failure 1"),
            FailureMode(description="Failure 2"),
        )
        output = PremortemOutput(failure_modes=modes)
        assert len(output.failure_modes) == 2

    @pytest.mark.unit
    def test_with_assumptions(self) -> None:
        """Output can contain assumptions."""
        assumptions = ("Users will adopt it", "Team has bandwidth")
        output = PremortemOutput(assumptions=assumptions)
        assert output.assumptions == assumptions

    @pytest.mark.unit
    def test_full_output(self) -> None:
        """Output can have all fields populated."""
        modes = (FailureMode(description="Failure 1"),)
        assumptions = ("Assumption 1",)
        output = PremortemOutput(
            failure_modes=modes,
            assumptions=assumptions,
        )
        assert len(output.failure_modes) == 1
        assert len(output.assumptions) == 1


class TestPremortemExecutor:
    """Tests for PremortemExecutor protocol."""

    @pytest.mark.unit
    def test_is_runtime_checkable(self) -> None:
        """PremortemExecutor is a runtime_checkable protocol."""
        executor = DefaultPremortemExecutor()
        assert isinstance(executor, PremortemExecutor)


class TestDefaultPremortemExecutor:
    """Tests for DefaultPremortemExecutor."""

    @pytest.mark.unit
    async def test_none_participation_returns_empty(self) -> None:
        """NONE participation returns empty output."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.NONE)

        async def dummy_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            return AgentResponse(agent_id=agent_id, content="response")

        output = await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1",),
            agent_caller=dummy_caller,
            config=config,
            token_budget=1000,
        )

        assert output.failure_modes == ()
        assert output.assumptions == ()

    @pytest.mark.unit
    async def test_empty_participants_returns_empty(self) -> None:
        """Empty participants returns empty output."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        async def dummy_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            return AgentResponse(agent_id=agent_id, content="response")

        output = await executor.execute(
            synthesis_text="Test decision",
            participant_ids=(),
            agent_caller=dummy_caller,
            config=config,
            token_budget=1000,
        )

        assert output == PremortemOutput()

    @pytest.mark.unit
    async def test_all_participation_calls_all(self) -> None:
        """ALL participation invokes all participants."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        called_agents = []

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            called_agents.append(agent_id)
            return AgentResponse(agent_id=agent_id, content="response")

        await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1", "agent_2", "agent_3"),
            agent_caller=tracking_caller,
            config=config,
            token_budget=1000,
        )

        assert len(called_agents) == 3
        assert set(called_agents) == {"agent_1", "agent_2", "agent_3"}

    @pytest.mark.unit
    async def test_strategic_participation_calls_subset(self) -> None:
        """STRATEGIC participation invokes approximately half."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.STRATEGIC)

        called_agents = []

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            called_agents.append(agent_id)
            return AgentResponse(agent_id=agent_id, content="response")

        await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1", "agent_2", "agent_3", "agent_4"),
            agent_caller=tracking_caller,
            config=config,
            token_budget=1000,
        )

        # Should call approximately half: 4 // 2 = 2
        assert len(called_agents) == 2
        assert set(called_agents) == {"agent_1", "agent_2"}

    @pytest.mark.unit
    async def test_token_distribution(self) -> None:
        """Tokens are distributed evenly across participants."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        token_allocations = []

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            token_allocations.append(tokens)
            return AgentResponse(agent_id=agent_id, content="response")

        await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1", "agent_2", "agent_3"),
            agent_caller=tracking_caller,
            config=config,
            token_budget=1500,
        )

        # Each of 3 agents should get 1500 // 3 = 500 tokens
        assert len(token_allocations) == 3
        assert all(t == 500 for t in token_allocations)

    @pytest.mark.unit
    async def test_token_distribution_with_remainder(self) -> None:
        """Remainder tokens are distributed to first participants."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        # Track by agent_id so assertions don't depend on TaskGroup run order.
        allocations: dict[str, int] = {}

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            allocations[agent_id] = tokens
            return AgentResponse(agent_id=agent_id, content="response")

        await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1", "agent_2", "agent_3"),
            agent_caller=tracking_caller,
            config=config,
            token_budget=1501,
        )

        # 1501 // 3 = 500 base, remainder 1 -> first agent (agent_1) gets 501
        assert len(allocations) == 3
        assert allocations == {"agent_1": 501, "agent_2": 500, "agent_3": 500}
        assert sum(allocations.values()) == 1501

    @pytest.mark.unit
    async def test_token_budget_smaller_than_participants(self) -> None:
        """When token_budget < participants, only first N agents are called."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        allocations: dict[str, int] = {}

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            allocations[agent_id] = tokens
            return AgentResponse(agent_id=agent_id, content="response")

        await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1", "agent_2", "agent_3", "agent_4"),
            agent_caller=tracking_caller,
            config=config,
            token_budget=2,
        )

        # base == 0 path: only first token_budget (2) agents called, 1 token each
        assert len(allocations) == 2
        assert allocations == {"agent_1": 1, "agent_2": 1}
        assert sum(allocations.values()) == 2

    @pytest.mark.unit
    async def test_prompt_contains_decision_summary(self) -> None:
        """Prompt includes the synthesis text."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        captured_prompts = []

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            captured_prompts.append(prompt)
            return AgentResponse(agent_id=agent_id, content="response")

        synthesis = "We will launch product X"
        await executor.execute(
            synthesis_text=synthesis,
            participant_ids=("agent_1",),
            agent_caller=tracking_caller,
            config=config,
            token_budget=1000,
        )

        assert len(captured_prompts) == 1
        assert synthesis in captured_prompts[0]

    @pytest.mark.unit
    async def test_prompt_asks_for_failure_modes(self) -> None:
        """Prompt asks agents to imagine failure."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        captured_prompts = []

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            captured_prompts.append(prompt)
            return AgentResponse(agent_id=agent_id, content="response")

        await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1",),
            agent_caller=tracking_caller,
            config=config,
            token_budget=1000,
        )

        prompt = captured_prompts[0]
        assert "fail" in prompt.lower() or "failed" in prompt.lower()
        assert "risk" in prompt.lower() or "assumption" in prompt.lower()

    @pytest.mark.unit
    async def test_parses_failure_responses(self) -> None:
        """Extracts failure modes from responses."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        async def failure_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            return AgentResponse(
                agent_id=agent_id,
                content="This could fail if the database goes down",
            )

        output = await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1",),
            agent_caller=failure_caller,
            config=config,
            token_budget=1000,
        )

        # Should extract at least one failure mode containing the keyword
        assert len(output.failure_modes) > 0
        assert any("database" in fm.description.lower() for fm in output.failure_modes)

    @pytest.mark.unit
    async def test_parses_assumption_responses(self) -> None:
        """Extracts assumptions from responses."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        async def assumption_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            return AgentResponse(
                agent_id=agent_id,
                content="Key assumption: our users will adopt this immediately",
            )

        output = await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1",),
            agent_caller=assumption_caller,
            config=config,
            token_budget=1000,
        )

        # Should extract at least one assumption containing the keyword
        assert len(output.assumptions) > 0
        assert any("adopt" in a.lower() for a in output.assumptions)

    @pytest.mark.unit
    async def test_multiple_responses_aggregated(self) -> None:
        """Multiple agent responses are aggregated."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        response_count = 0

        async def counting_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            nonlocal response_count
            response_count += 1
            return AgentResponse(
                agent_id=agent_id,
                content=(
                    "Risk: system might fail under load. Assumption: team has skills."
                ),
            )

        output = await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1", "agent_2"),
            agent_caller=counting_caller,
            config=config,
            token_budget=2000,
        )

        assert response_count == 2
        # Both responses contribute to aggregation
        assert len(output.failure_modes) + len(output.assumptions) > 0

    @pytest.mark.unit
    async def test_returns_valid_premortem_output(self) -> None:
        """Result is always a valid PremortemOutput."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)

        async def dummy_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            return AgentResponse(agent_id=agent_id, content="response")

        output = await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1",),
            agent_caller=dummy_caller,
            config=config,
            token_budget=1000,
        )

        assert isinstance(output, PremortemOutput)
        assert isinstance(output.failure_modes, tuple)
        assert isinstance(output.assumptions, tuple)
        assert all(isinstance(m, FailureMode) for m in output.failure_modes)

    @pytest.mark.unit
    async def test_strategic_odd_number_participants(self) -> None:
        """STRATEGIC with odd number rounds down."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.STRATEGIC)

        called_agents = []

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            called_agents.append(agent_id)
            return AgentResponse(agent_id=agent_id, content="response")

        # 5 participants: 5 // 2 = 2
        await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("a1", "a2", "a3", "a4", "a5"),
            agent_caller=tracking_caller,
            config=config,
            token_budget=2000,
        )

        assert len(called_agents) == 2

    @pytest.mark.unit
    async def test_strategic_single_participant(self) -> None:
        """STRATEGIC with single participant calls that one."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.STRATEGIC)

        called_agents = []

        async def tracking_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del context_id
            called_agents.append(agent_id)
            return AgentResponse(agent_id=agent_id, content="response")

        # 1 participant: max(1, 1 // 2) = 1
        await executor.execute(
            synthesis_text="Test decision",
            participant_ids=("agent_1",),
            agent_caller=tracking_caller,
            config=config,
            token_budget=1000,
        )

        assert len(called_agents) == 1
        assert called_agents[0] == "agent_1"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("context_id_input", "participant_ids", "expected_seen"),
        [
            # Caller-supplied id flows through to every participant.
            ("ctx-123", ("agent_1", "agent_2"), ["ctx-123", "ctx-123"]),
            # Omitted -> ``execute`` default applies the
            # ``_PREMORTEM_TASK_ID_FALLBACK`` constant.
            (None, ("agent_1",), ["system:premortem"]),
            # Whitespace-only -> ``execute`` strips and substitutes
            # the fallback so ``cost_recording_scope`` never sees a
            # blank task id.
            ("   ", ("agent_1",), ["system:premortem"]),
        ],
        ids=["explicit", "default-fallback", "blank-normalised"],
    )
    async def test_context_id_forwarding(
        self,
        context_id_input: str | None,
        participant_ids: tuple[str, ...],
        expected_seen: list[str],
    ) -> None:
        """``context_id`` flows through ``execute`` to ``agent_caller``."""
        executor = DefaultPremortemExecutor()
        config = PremortemConfig(participants=PremortemParticipation.ALL)
        seen_context_ids: list[str] = []

        async def capturing_caller(
            agent_id: str, prompt: str, tokens: int, context_id: str
        ) -> AgentResponse:
            del prompt, tokens
            seen_context_ids.append(context_id)
            return AgentResponse(agent_id=agent_id, content="response")

        kwargs: dict[str, object] = {
            "synthesis_text": "Test decision",
            "participant_ids": participant_ids,
            "agent_caller": capturing_caller,
            "config": config,
            "token_budget": 1000,
        }
        if context_id_input is not None:
            kwargs["context_id"] = context_id_input
        await executor.execute(**kwargs)  # type: ignore[arg-type]

        assert seen_context_ids == expected_seen
