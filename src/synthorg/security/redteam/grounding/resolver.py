# module-kind: declarative
"""Lazy dependency resolver for the substrate-backed grounding checker.

The substrate checker is constructed at boot (in
:func:`synthorg.security.redteam.builder.build_red_team_runtime`) BEFORE
the knowledge substrate and provider registry finish wiring. Rather than
capture those services by value at construction time, the checker holds a
:data:`GroundingSubstrateResolver` and calls it on every
:meth:`~synthorg.security.redteam.grounding.protocol.GroundingChecker.check`,
reading whatever is live on the application state at that moment.

A resolver returning ``None`` (or a context whose ``knowledge_service``
is ``None``) signals the substrate is not yet available; the checker
then degrades to the deterministic heuristic rather than blocking on a
gap the operator did not ask for.
"""

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.tracker import CostTracker
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.service import KnowledgeService
from synthorg.providers.protocol import CompletionProvider


class GroundingSubstrateContext(BaseModel):
    """Live dependencies the substrate checker resolves at check time.

    Construction is via the resolver closure built over the application
    state, not direct instantiation in business logic. ``knowledge_service``
    and ``provider`` are non-Pydantic runtime objects, permitted by
    ``arbitrary_types_allowed``.

    Attributes:
        knowledge_service: The wired project-scoped knowledge service, or
            ``None`` when the substrate has not finished wiring (the
            checker degrades to the heuristic in that case).
        provider: Completion provider driver used for claim extraction
            and entailment.
        model_id: Model identifier passed to ``provider.complete``.
        cost_tracker: Optional cost sink; when present, extraction and
            entailment calls are attributed through ``cost_recording_scope``.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    knowledge_service: KnowledgeService | None = Field(
        default=None,
        description="Wired knowledge service, or None until it wires",
    )
    provider: CompletionProvider = Field(
        description="Completion provider for extraction + entailment",
    )
    model_id: NotBlankStr = Field(description="Model id for provider.complete")
    cost_tracker: CostTracker | None = Field(
        default=None,
        description="Optional cost sink for LLM-call attribution",
    )


#: A no-argument callable resolving the live substrate dependencies, or
#: ``None`` when no provider is available at all. Built as a closure over
#: the application state in ``workers/_red_team_runtime.py`` so each
#: ``check()`` reads the current (hot-swappable) provider registry and
#: knowledge service.
GroundingSubstrateResolver = Callable[[], "GroundingSubstrateContext | None"]
