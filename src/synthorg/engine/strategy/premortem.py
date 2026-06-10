"""Premortem analysis for strategic decision-making.

Implements "premortem" analysis where participants imagine a decision has
failed and work backward to identify likely failure modes, risks, and
hidden assumptions. This helps surface weaknesses in a proposal before
execution.
"""

import asyncio
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.communication.meeting.models import AgentResponse
from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.models import (
    PremortemConfig,
    PremortemParticipation,
)
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import (
    STRATEGY_PREMORTEM_RESPONSE_SKIPPED,
)

logger = get_logger(__name__)

_MIN_RESPONSE_LENGTH: int = 10


class FailureMode(BaseModel):
    """A potential failure mode identified during premortem.

    Attributes:
        description: Description of how the decision could fail.
        likelihood: Likelihood of this failure mode (low/medium/high).
        impact: Impact severity if this failure occurs (low/medium/high).
        mitigation: Proposed mitigation or preventive action.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    description: NotBlankStr = Field(
        description="Description of how the decision could fail"
    )
    likelihood: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Likelihood of this failure mode",
    )
    impact: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Impact severity if this failure occurs",
    )
    mitigation: NotBlankStr = Field(
        default="No mitigation identified",
        description="Proposed mitigation or preventive action",
    )


class PremortemOutput(BaseModel):
    """Aggregated output from premortem analysis.

    Attributes:
        failure_modes: Potential failure modes identified.
        assumptions: Key assumptions underlying the decision.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    failure_modes: tuple[FailureMode, ...] = Field(
        default=(),
        description="Potential failure modes identified",
    )
    assumptions: tuple[str, ...] = Field(
        default=(),
        description="Key assumptions underlying the decision",
    )


_PREMORTEM_TASK_ID_FALLBACK = "system:premortem"


@runtime_checkable
class PremortemExecutor(Protocol):
    """Execute premortem analysis on meeting synthesis.

    Implementations conduct a premortem activity where participants
    imagine a decision has failed and work backward to identify risks,
    failure modes, and hidden assumptions.
    """

    async def execute(  # noqa: PLR0913
        self,
        *,
        synthesis_text: str,
        participant_ids: tuple[NotBlankStr, ...],
        agent_caller: AgentCaller,
        config: PremortemConfig,
        token_budget: int,
        context_id: str = _PREMORTEM_TASK_ID_FALLBACK,
    ) -> PremortemOutput:
        """Conduct premortem analysis.

        Args:
            synthesis_text: Summary of the decision/proposal to analyze.
            participant_ids: IDs of agents participating in premortem.
            agent_caller: Callback to invoke agents.
            config: Premortem configuration.
            token_budget: Maximum tokens for all premortem calls.
            context_id: Threaded as the ``meeting_id`` argument of
                ``agent_caller`` so cost-recording attribution carries
                a meaningful task identifier.  Defaults to
                ``system:premortem`` when no caller-supplied context
                is available.

        Returns:
            Aggregated premortem analysis output.
        """
        ...


class DefaultPremortemExecutor:
    """Default premortem executor with configurable participant selection.

    Conducts parallel premortem by invoking selected participants with
    a structured prompt asking them to imagine the decision failed and
    identify failure modes, risks, and assumptions. Aggregates results
    into a unified output.
    """

    async def execute(  # noqa: PLR0913
        self,
        *,
        synthesis_text: str,
        participant_ids: tuple[NotBlankStr, ...],
        agent_caller: AgentCaller,
        config: PremortemConfig,
        token_budget: int,
        context_id: str = _PREMORTEM_TASK_ID_FALLBACK,
    ) -> PremortemOutput:
        """Conduct premortem analysis.

        Args:
            synthesis_text: Summary of the decision/proposal to analyze.
            participant_ids: IDs of agents participating in premortem.
            agent_caller: Callback to invoke agents.
            config: Premortem configuration.
            token_budget: Maximum tokens for all premortem calls.
            context_id: Threaded as the ``meeting_id`` argument of
                ``agent_caller`` so cost-recording attribution carries
                a meaningful task identifier.  Defaults to
                ``system:premortem`` when no caller-supplied context
                is available.

        Returns:
            Aggregated premortem analysis output.
        """
        selected = self._select_participants(participant_ids, config)
        if not selected:
            return PremortemOutput()

        # Normalise blank/whitespace-only ``context_id`` to the
        # fallback so the downstream ``agent_caller`` (and the
        # ``cost_recording_scope`` it opens) never sees a
        # whitespace-only ``task_id`` that would fail
        # ``NotBlankStr`` validation deeper in the stack.
        normalized_context_id = context_id.strip() or _PREMORTEM_TASK_ID_FALLBACK
        prompt = self._build_prompt(synthesis_text)
        responses = await self._gather_responses(
            agent_caller,
            selected,
            prompt,
            token_budget,
            context_id=normalized_context_id,
        )
        failure_modes, assumptions = self._aggregate_responses(responses)

        return PremortemOutput(
            failure_modes=tuple(failure_modes),
            assumptions=tuple(assumptions),
        )

    @staticmethod
    def _select_participants(
        participant_ids: tuple[NotBlankStr, ...],
        config: PremortemConfig,
    ) -> tuple[NotBlankStr, ...]:
        """Select participants based on config.

        Returns:
            Empty tuple when ``PremortemParticipation.NONE``; the first
            half (rounded down to at least one) when ``STRATEGIC``; the
            full ``participant_ids`` tuple when ``ALL``.
        """
        if config.participants == PremortemParticipation.NONE:
            return ()
        if config.participants == PremortemParticipation.STRATEGIC:
            half = max(1, len(participant_ids) // 2)
            return participant_ids[:half]
        return participant_ids

    @staticmethod
    def _build_prompt(synthesis_text: str) -> str:
        """Build the premortem prompt.

        Returns:
            The structured prompt the participating agents receive.
        """
        return (
            f"The following decision has been made:\n\n{synthesis_text}\n\n"
            "Imagine this decision was implemented and failed spectacularly. "
            "Working backward from the failure, identify:\n"
            "1. How did it fail? (describe the failure mode)\n"
            "2. What is the likelihood? (low/medium/high)\n"
            "3. What would be the impact? (low/medium/high)\n"
            "4. How could it have been prevented? (mitigation)\n\n"
            "Also identify key assumptions underlying this decision that "
            "could be wrong. Be specific and concrete."
        )

    @staticmethod
    async def _gather_responses(
        agent_caller: AgentCaller,
        selected: tuple[NotBlankStr, ...],
        prompt: str,
        token_budget: int,
        *,
        context_id: str,
    ) -> list[AgentResponse]:
        """Fan-out agent calls and collect responses.

        Returns:
            The list of :class:`AgentResponse` from agents that
            returned (non-critical exceptions yield no entry).
        """
        n = len(selected)
        base = token_budget // n
        remainder = token_budget % n

        # When budget < agents, only the first token_budget agents
        # get 1 token each; the rest are skipped.
        if base == 0:
            active = selected[: min(n, token_budget)]
            tokens_list = [1] * len(active)
        else:
            active = selected
            tokens_list = [base + (1 if i < remainder else 0) for i in range(n)]

        result_slots: list[AgentResponse | None] = [None] * len(active)

        async def _call_and_store(idx: int, pid: NotBlankStr) -> None:
            try:
                result_slots[idx] = await agent_caller(
                    pid,
                    prompt,
                    tokens_list[idx],
                    context_id,
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    STRATEGY_PREMORTEM_RESPONSE_SKIPPED,
                    participant_id=str(pid),
                    reason="agent_caller raised",
                )

        async with asyncio.TaskGroup() as tg:
            for idx, pid in enumerate(active):
                _ = tg.create_task(_call_and_store(idx, pid))

        return [r for r in result_slots if r is not None]

    @staticmethod
    def _aggregate_responses(
        responses: list[AgentResponse],
    ) -> tuple[list[FailureMode], list[str]]:
        """Parse failure modes and assumptions from responses.

        Returns:
            ``(failure_modes, assumptions)`` extracted heuristically
            from each agent response's content.
        """
        all_failure_modes: list[FailureMode] = []
        all_assumptions: list[str] = []

        for response in responses:
            content = response.content
            if len(content) <= _MIN_RESPONSE_LENGTH:
                logger.debug(
                    STRATEGY_PREMORTEM_RESPONSE_SKIPPED,
                    content_length=len(content),
                    min_length=_MIN_RESPONSE_LENGTH,
                )
                continue

            lower_content = content.lower()

            if any(
                word in lower_content for word in ["fail", "risk", "problem", "wrong"]
            ):
                desc = content[:200].strip()
                if desc:
                    all_failure_modes.append(
                        FailureMode(
                            description=desc,
                            mitigation="See full premortem response",
                        )
                    )

            if "assumption" in lower_content:
                assumption = content[:150].strip()
                if assumption:
                    all_assumptions.append(assumption)

        return all_failure_modes, all_assumptions
