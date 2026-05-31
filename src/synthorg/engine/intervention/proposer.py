"""Pluggable supersession proposer for mid-flight steering.

When the operator issues a redirect in ``PROPOSE`` mode, a proposer refines the
set of now-obsolete sibling tasks and returns it for the operator to confirm or
edit before any cancellation. The proposer NEVER cancels; only a confirmed
operator call cancels. The default no-op proposer echoes the operator's seed set;
the LLM proposer asks a provider which in-flight tasks the directive makes
obsolete.
"""

import json
import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.models import SteeringSupersessionProposal
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import STEERING_PROPOSE_FAILED
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_PROPOSER_TEMPERATURE: float = 0.1
_PROPOSER_MAX_TOKENS: int = 1024

_SYSTEM_PROMPT = (
    "You assess which in-flight tasks an operator steering directive makes "
    "obsolete. Return ONLY a JSON object of the form "
    '{"obsolete_task_ids": ["id1", "id2"], "rationale": "..."}. '
    "Only list ids from the candidate set; never invent ids. If none are "
    "obsolete, return an empty list."
)


@runtime_checkable
class SteeringSupersessionProposer(Protocol):
    """Refines the obsolete-task set for a steering directive."""

    async def propose(
        self,
        *,
        directive_id: NotBlankStr,
        directive_text: str,
        candidate_tasks: tuple[Task, ...],
        seed_task_ids: tuple[NotBlankStr, ...],
    ) -> SteeringSupersessionProposal:
        """Return a proposed obsolete-task set; never cancels anything."""
        ...


class NoOpSupersessionProposer:
    """Echoes the operator's seed set unchanged (no LLM refinement)."""

    async def propose(
        self,
        *,
        directive_id: NotBlankStr,
        directive_text: str,  # noqa: ARG002 -- protocol param unused here
        candidate_tasks: tuple[Task, ...],  # noqa: ARG002 -- protocol param unused
        seed_task_ids: tuple[NotBlankStr, ...],
    ) -> SteeringSupersessionProposal:
        """Return the seed set as the proposal.

        Returns:
            A proposal echoing ``seed_task_ids``.
        """
        return SteeringSupersessionProposal(
            directive_id=directive_id,
            proposed_task_ids=seed_task_ids,
            rationale="No proposer configured; operator selection unchanged.",
        )


class LLMSupersessionProposer:
    """Provider-backed proposer: asks an LLM which tasks are obsolete."""

    def __init__(
        self,
        provider: CompletionProvider,
        *,
        model: str,
    ) -> None:
        self._provider = provider
        self._model = model

    async def propose(
        self,
        *,
        directive_id: NotBlankStr,
        directive_text: str,
        candidate_tasks: tuple[Task, ...],
        seed_task_ids: tuple[NotBlankStr, ...],
    ) -> SteeringSupersessionProposal:
        """Ask the provider which candidate tasks the directive obsoletes.

        Best-effort: a provider or parse failure falls back to the operator's
        seed set so the redirect is never blocked by the refinement step.

        Returns:
            A proposal whose ids are a subset of the candidate ids.
        """
        if not candidate_tasks:
            return SteeringSupersessionProposal(
                directive_id=directive_id,
                proposed_task_ids=seed_task_ids,
                rationale="No in-flight tasks to refine.",
            )
        candidate_ids = {str(t.id) for t in candidate_tasks}
        try:
            response = await self._provider.complete(
                messages=self._build_messages(directive_text, candidate_tasks),
                model=self._model,
                tools=None,
                config=CompletionConfig(
                    temperature=_PROPOSER_TEMPERATURE,
                    max_tokens=_PROPOSER_MAX_TOKENS,
                ),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                STEERING_PROPOSE_FAILED,
                directive_id=directive_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return self._seed_fallback(directive_id, seed_task_ids)

        proposed, rationale = _parse_proposal(response.content or "", candidate_ids)
        return SteeringSupersessionProposal(
            directive_id=directive_id,
            proposed_task_ids=tuple(NotBlankStr(i) for i in proposed),
            rationale=rationale,
        )

    @staticmethod
    def _seed_fallback(
        directive_id: NotBlankStr,
        seed_task_ids: tuple[NotBlankStr, ...],
    ) -> SteeringSupersessionProposal:
        return SteeringSupersessionProposal(
            directive_id=directive_id,
            proposed_task_ids=seed_task_ids,
            rationale="Proposer unavailable; operator selection unchanged.",
        )

    @staticmethod
    def _build_messages(
        directive_text: str,
        candidate_tasks: tuple[Task, ...],
    ) -> list[ChatMessage]:
        lines = [
            f"- id={t.id} title={t.title} description={t.description}"
            for t in candidate_tasks
        ]
        body = (
            f"Steering directive:\n{directive_text}\n\n"
            f"Candidate in-flight tasks:\n" + "\n".join(lines)
        )
        return [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=(
                    f"{_SYSTEM_PROMPT}\n\n"
                    f"{untrusted_content_directive((TAG_TASK_DATA,))}"
                ),
            ),
            ChatMessage(
                role=MessageRole.USER,
                content=wrap_untrusted(TAG_TASK_DATA, body),
            ),
        ]


def _parse_proposal(
    content: str,
    candidate_ids: set[str],
) -> tuple[tuple[str, ...], str]:
    """Parse the LLM JSON, keeping only ids in the candidate set.

    Returns:
        ``(obsolete_task_ids, rationale)`` restricted to known candidate ids.
    """
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match is None:
        return (), ""
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError, ValueError:
        return (), ""
    raw_ids = parsed.get("obsolete_task_ids", []) if isinstance(parsed, dict) else []
    ids = tuple(
        str(i) for i in raw_ids if isinstance(i, str) and str(i) in candidate_ids
    )
    rationale = ""
    if isinstance(parsed, dict) and isinstance(parsed.get("rationale"), str):
        rationale = parsed["rationale"]
    return ids, rationale


def build_supersession_proposer(
    provider: CompletionProvider | None,
    *,
    model: str | None = None,
    enabled: bool = True,
) -> SteeringSupersessionProposer:
    """Select the supersession proposer implementation.

    Returns:
        An :class:`LLMSupersessionProposer` when a provider, a model, and the
        feature flag are all present; otherwise a :class:`NoOpSupersessionProposer`.
    """
    if provider is not None and model is not None and enabled:
        return LLMSupersessionProposer(provider, model=model)
    return NoOpSupersessionProposer()
