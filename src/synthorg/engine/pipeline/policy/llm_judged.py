"""LLM-judged routing policy.

Asks a completion provider for a binary leaf/splittable verdict over
the (untrusted-wrapped) task title and description. Falls back to an
injected deterministic policy when the model response cannot be
parsed, so the spine never stalls on an ambiguous answer.
"""

import re
from typing import TYPE_CHECKING, Final

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import RoutingVerdict
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import PIPELINE_ROUTING_DECIDED
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

if TYPE_CHECKING:
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.pipeline.policy.protocol import WorkRoutingPolicy
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_LLM_TEMPERATURE: Final[float] = 0.0
_LLM_MAX_OUTPUT_TOKENS: Final[int] = 16

_SPLITTABLE_RE: Final = re.compile(r"\bsplittable\b")
_LEAF_RE: Final = re.compile(r"\bleaf\b")
_NEGATION_RE: Final = re.compile(r"\b(?:not|no|never)\b|n't")

_SYSTEM_PROMPT: Final[str] = (
    "You are a work-routing classifier for a virtual software "
    "organisation. Decide whether a unit of work should be executed "
    "by a SINGLE agent or split across a TEAM. Answer with exactly "
    "one word: LEAF for single-agent work, or SPLITTABLE for work "
    "that benefits from decomposition across multiple agents.\n\n"
    + untrusted_content_directive((TAG_TASK_DATA,))
)


class LlmJudgedRoutingPolicy:
    """Routing policy backed by a completion provider.

    Args:
        provider: The completion provider (shared boot provider).
        model: Model identifier for the classification call.
        fallback: Deterministic policy used when the model response
            is unparseable (keeps the decision total and reproducible).
        cost_tracker: Optional cost tracker; when wired the call is
            recorded through the cost chokepoint.
    """

    __slots__ = ("_cost_tracker", "_fallback", "_model", "_provider")

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: str,
        fallback: WorkRoutingPolicy,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        if not model or not model.strip():
            msg = "model must be a non-blank string"
            raise ValueError(msg)
        self._provider = provider
        self._model = model
        self._fallback = fallback
        self._cost_tracker = cost_tracker

    async def decide(
        self,
        *,
        task: Task,
        available_agents: tuple[AgentIdentity, ...],
    ) -> RoutingVerdict:
        """Classify *task* via the provider, falling back when ambiguous.

        Returns:
            The :class:`RoutingVerdict` parsed from the LLM response;
            when the response is ambiguous, the verdict from the
            deterministic fallback policy.
        """
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(
                role=MessageRole.USER,
                content=wrap_untrusted(
                    TAG_TASK_DATA,
                    f"Title: {task.title}\n\nDescription: {task.description}",
                ),
            ),
        ]
        config = CompletionConfig(
            temperature=_LLM_TEMPERATURE,
            max_tokens=_LLM_MAX_OUTPUT_TOKENS,
        )
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=NotBlankStr("system"),
            task_id=str(task.id),
            call_category=LLMCallCategory.SYSTEM,
        ):
            response = await self._provider.complete(
                messages,
                self._model,
                config=config,
            )

        verdict = self._parse_verdict(response.content)
        if verdict is not None:
            logger.info(
                PIPELINE_ROUTING_DECIDED,
                task_id=str(task.id),
                policy="llm-judged",
                verdict=verdict.value,
            )
            return verdict

        logger.info(
            PIPELINE_ROUTING_DECIDED,
            task_id=str(task.id),
            policy="llm-judged",
            verdict="unparseable",
            note="falling back to deterministic policy",
        )
        return await self._fallback.decide(
            task=task,
            available_agents=available_agents,
        )

    @staticmethod
    def _parse_verdict(content: str | None) -> RoutingVerdict | None:
        """Extract a verdict from model text, or ``None`` if ambiguous.

        Whole-word matching with negation detection. A response that
        mentions both verdict words, or a negated / qualified one
        (e.g. ``"not splittable"``), is treated as ambiguous so the
        caller falls back to the deterministic policy instead of
        acting on a misread verdict.

        Returns:
            The :class:`RoutingVerdict` parsed from the text; ``None``
            when the response is missing, ambiguous, or negated.
        """
        if content is None:
            return None
        text = content.strip().lower()
        if not text:
            return None
        has_splittable = _SPLITTABLE_RE.search(text) is not None
        has_leaf = _LEAF_RE.search(text) is not None
        if has_splittable and has_leaf:
            return None
        negated = _NEGATION_RE.search(text) is not None
        if has_splittable:
            return None if negated else RoutingVerdict.SPLITTABLE
        if has_leaf:
            return None if negated else RoutingVerdict.LEAF
        return None
