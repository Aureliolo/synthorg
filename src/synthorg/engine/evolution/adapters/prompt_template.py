"""PromptTemplateAdapter -- injects learned memories into prompt slots."""

from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.adapters._memory_store import (
    store_proposal_as_memory,
)
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger

logger = get_logger(__name__)


class PromptTemplateAdapter:
    """Injects learned memories into prompt slots.

    Stores prompt template changes as procedural memories with the tag
    "evolution-prompt-injection" for injection into future prompts.
    """

    def __init__(self, memory_backend: MemoryBackend) -> None:
        """Initialize PromptTemplateAdapter.

        Args:
            memory_backend: Memory storage backend.
        """
        self._memory_backend = memory_backend

    @property
    def name(self) -> str:
        """Return adapter name."""
        return "PromptTemplateAdapter"

    @property
    def axis(self) -> AdaptationAxis:
        """Return the adaptation axis this adapter handles."""
        return AdaptationAxis.PROMPT_TEMPLATE

    async def apply(
        self,
        proposal: AdaptationProposal,
        agent_id: NotBlankStr,
    ) -> None:
        """Apply the prompt template adaptation.

        Stores the proposal as a procedural memory entry tagged with
        "evolution-prompt-injection" for injection into system prompts.

        Args:
            proposal: The approved proposal to apply.
            agent_id: Target agent.

        Raises:
            Exception: If the memory store operation fails.
        """
        await store_proposal_as_memory(
            self._memory_backend,
            proposal,
            agent_id,
            "evolution-prompt-injection",
        )
