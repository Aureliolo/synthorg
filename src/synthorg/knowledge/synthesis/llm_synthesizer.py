"""LLM-backed knowledge synthesiser.

Presents the retrieved chunks (wrapped as untrusted) to the model and asks for
a structured answer whose claims cite chunks by ``ref_id``. The
:class:`KnowledgeCitationBinder` then validates every cited reference resolves
to a retrieved chunk, so an emitted answer is always citation-backed.
"""

import json
from typing import Final

from pydantic import ValidationError

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_KNOWLEDGE,
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.knowledge.constants import (
    KNOWLEDGE_SYNTHESIS_DEFAULT_MAX_CHUNKS,
    KNOWLEDGE_SYNTHESIS_MIN_HITS,
)
from synthorg.knowledge.errors import KnowledgeSynthesisError
from synthorg.knowledge.models import (
    KnowledgeAnswer,
    KnowledgeAnswerClaim,
    KnowledgeHit,
)
from synthorg.knowledge.synthesis._args import KnowledgeSynthesisOutput
from synthorg.knowledge.synthesis.citation_binder import KnowledgeCitationBinder
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_SYNTHESIS_FAILED,
    KNOWLEDGE_SYNTHESIS_OUTPUT_INVALID,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.structured_text import complete_text, extract_json_object

logger = get_logger(__name__)

_SYNTHESIS_BOUNDARY: Final[str] = "knowledge.synthesis"
_SYNTHESIS_TASK_ID: Final[NotBlankStr] = NotBlankStr("system:knowledge:synthesis")

_SYSTEM_PROMPT: Final[str] = (
    "You are a knowledge-base assistant. Using ONLY the provided sources, "
    "answer the question concisely and accurately. Every claim must cite one "
    "or more sources by their exact ref_id. Return ONLY a JSON object:\n"
    '{"answer": "<prose answer>", "claims": [{"text": "<claim>", '
    '"claim_type": "<fact|analysis|recommendation|comparison>", '
    '"confidence": <0..1>, "ref_ids": ["<source ref_id>"]}]}\n'
    "Do not invent sources or cite a ref_id that is not listed. If the sources "
    "do not answer the question, say so in a single claim citing the closest "
    "source. " + untrusted_content_directive((TAG_KNOWLEDGE, TAG_TASK_DATA))
)


def _ref_id(index: int) -> str:
    """Return the stable per-hit reference id for the *index*-th chunk."""
    return f"src-{index}"


class KnowledgeSynthesizer:
    """Produces a citation-backed answer with one deterministic LLM call."""

    __slots__ = (
        "_binder",
        "_clock",
        "_cost_tracker",
        "_max_chunks",
        "_model",
        "_provider",
    )

    def __init__(  # noqa: PLR0913 -- injected synthesis collaborators
        self,
        *,
        provider: CompletionProvider,
        model: str,
        binder: KnowledgeCitationBinder,
        max_chunks: int = KNOWLEDGE_SYNTHESIS_DEFAULT_MAX_CHUNKS,
        clock: Clock | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._binder = binder
        self._max_chunks = max(1, max_chunks)
        self._clock = clock if clock is not None else SystemClock()
        self._cost_tracker = cost_tracker

    @property
    def max_chunks(self) -> int:
        """Maximum retrieved chunks consulted per answer (the model budget)."""
        return self._max_chunks

    async def synthesize(
        self,
        *,
        query: NotBlankStr,
        hits: tuple[KnowledgeHit, ...],
        project_id: NotBlankStr | None = None,
    ) -> tuple[KnowledgeAnswer, float]:
        """Return a cited answer and the cost of producing it.

        Returns:
            A tuple of the cited ``KnowledgeAnswer`` and the cost of
            producing it.

        Raises:
            KnowledgeSynthesisError: When too few chunks were retrieved to
                ground an answer, or the model output cannot be parsed, or a
                claim cites a chunk that was not retrieved.
        """
        if len(hits) < KNOWLEDGE_SYNTHESIS_MIN_HITS:
            msg = "insufficient grounding: no retrieved chunks to synthesise over"
            logger.warning(
                KNOWLEDGE_SYNTHESIS_FAILED,
                reason="insufficient_grounding",
                project_id=project_id,
                hit_count=len(hits),
                error_type=KnowledgeSynthesisError.__name__,
            )
            raise KnowledgeSynthesisError(msg)
        selected = hits[: self._max_chunks]
        hits_by_ref = {_ref_id(index): hit for index, hit in enumerate(selected)}
        content, cost = await complete_text(
            self._provider,
            self._model,
            system=_SYSTEM_PROMPT,
            user=self._build_user_prompt(query, hits_by_ref),
            cost_tracker=self._cost_tracker,
            task_id=_SYNTHESIS_TASK_ID,
            project_id=project_id,
        )
        answer = self._build_answer(
            query=query,
            output=self._parse(content),
            hits_by_ref=hits_by_ref,
            chunks_consulted=len(selected),
        )
        return answer, cost

    def _build_answer(
        self,
        *,
        query: NotBlankStr,
        output: KnowledgeSynthesisOutput,
        hits_by_ref: dict[str, KnowledgeHit],
        chunks_consulted: int,
    ) -> KnowledgeAnswer:
        """Bind the parsed output's claims to citations and assemble the answer.

        Returns:
            The citation-bound ``KnowledgeAnswer``.

        Raises:
            KnowledgeSynthesisError: When a claim cites an unretrieved chunk.
        """
        claims = tuple(
            KnowledgeAnswerClaim(
                text=claim.text,
                claim_type=claim.claim_type,
                citations=self._binder.resolve(claim.ref_ids, hits_by_ref),
                confidence=claim.confidence,
            )
            for claim in output.claims
        )
        return KnowledgeAnswer(
            query=query,
            answer=output.answer,
            claims=claims,
            chunks_consulted=chunks_consulted,
            synthesis_model=NotBlankStr(self._model),
            created_at=self._clock.now(),
        )

    @staticmethod
    def _build_user_prompt(
        query: NotBlankStr,
        hits_by_ref: dict[str, KnowledgeHit],
    ) -> str:
        """Build the user prompt: the fenced question and wrapped chunk blocks.

        The ``ref_id`` is trusted (assigned by the synthesiser); the chunk
        title and text come from the ingested corpus, so they are wrapped
        together inside one fence. The question is operator-supplied input,
        fenced as task data.

        Returns:
            The assembled user prompt.
        """
        question = wrap_untrusted(TAG_TASK_DATA, f"Question: {query}")
        blocks = [
            f"ref_id: {ref_id}\n"
            + wrap_untrusted(
                TAG_KNOWLEDGE,
                f"title: {hit.citation.title}\n{hit.chunk_text}",
            )
            for ref_id, hit in hits_by_ref.items()
        ]
        return f"{question}\n\nSources:\n" + "\n\n".join(blocks)

    def _parse(self, content: str) -> KnowledgeSynthesisOutput:
        """Extract and validate the synthesiser's structured output.

        Returns:
            The parsed, validated ``KnowledgeSynthesisOutput``.

        Raises:
            KnowledgeSynthesisError: When the model output is not parseable
                into a valid ``KnowledgeSynthesisOutput``.
        """
        try:
            obj = json.loads(extract_json_object(content))
            return parse_typed(_SYNTHESIS_BOUNDARY, obj, KnowledgeSynthesisOutput)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                KNOWLEDGE_SYNTHESIS_OUTPUT_INVALID,
                stage="synthesis",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "synthesiser returned unparseable output"
            raise KnowledgeSynthesisError(msg) from exc
