"""Factory wiring for the knowledge synthesiser.

Selects the synthesis strategy behind the ``KnowledgeSynthesizerKind``
discriminator and returns a wired :class:`Synthesizer`. ``llm`` is the only
shipped implementation; a new strategy extends the union AND this factory in
lockstep, so an unmatched discriminator fails at wiring time, not at first ask.
"""

from typing import Literal

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.clock import Clock
from synthorg.knowledge.constants import KNOWLEDGE_SYNTHESIS_DEFAULT_MAX_CHUNKS
from synthorg.knowledge.errors import KnowledgeSynthesisError
from synthorg.knowledge.synthesis.citation_binder import KnowledgeCitationBinder
from synthorg.knowledge.synthesis.llm_synthesizer import KnowledgeSynthesizer
from synthorg.knowledge.synthesis.protocol import Synthesizer
from synthorg.observability import get_logger
from synthorg.observability.events.knowledge import KNOWLEDGE_SYNTHESIZER_KIND_UNKNOWN
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

KnowledgeSynthesizerKind = Literal["llm"]
"""Discriminator for the knowledge synthesis strategy. ``llm`` is the only
shipped implementation; the synthesiser cites chunks by reference id and a
binder validates every citation resolves before the answer is emitted."""


def build_knowledge_synthesizer(
    *,
    kind: str = "llm",
    provider: CompletionProvider,
    model: str,
    max_chunks: int = KNOWLEDGE_SYNTHESIS_DEFAULT_MAX_CHUNKS,
    clock: Clock | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
) -> Synthesizer:
    """Build the knowledge synthesiser for the configured strategy.

    Returns:
        A wired :class:`Synthesizer`.

    Raises:
        KnowledgeSynthesisError: When *kind* names no known strategy.
    """
    if kind == "llm":
        return KnowledgeSynthesizer(
            provider=provider,
            model=model,
            binder=KnowledgeCitationBinder(),
            max_chunks=max_chunks,
            clock=clock,
            cost_tracker=cost_tracker,
        )
    logger.warning(KNOWLEDGE_SYNTHESIZER_KIND_UNKNOWN, kind=kind)
    msg = f"unknown knowledge synthesizer kind: {kind!r}"
    raise KnowledgeSynthesisError(msg)
