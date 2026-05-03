"""Hierarchical retriever domain models.

Models for supervisor routing decisions and reflective retry
corrections.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.retrieval.models import RetrievalQuery  # noqa: TC001


class WorkerRoutingDecision(BaseModel):
    """Supervisor's routing output selecting which workers to invoke.

    Attributes:
        selected_workers: Worker names to invoke (e.g. ``("semantic",
            "episodic")``).
        reason: Explanation for the routing choice.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    selected_workers: tuple[NotBlankStr, ...] = Field(
        description="Worker names to invoke",
    )
    reason: NotBlankStr = Field(
        description="Explanation for the routing choice",
    )


class RetrievalRetryCorrection(BaseModel):
    """Supervisor's retry guidance after a low-quality retrieval.

    Emitted when the supervisor determines that the initial retrieval
    produced insufficient results and a corrective retry is warranted.

    Attributes:
        corrected_query: Modified query for retry (``None`` = keep
            original).
        alternative_strategy: Fallback routing strategy (``None`` =
            re-use original routing).
        reason: Why the retry is needed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    corrected_query: RetrievalQuery | None = Field(
        default=None,
        description="Modified query for retry (None = keep original)",
    )
    alternative_strategy: Literal["semantic_only", "episodic_only", "skip"] | None = (
        Field(
            default=None,
            description="Fallback routing strategy (None = re-use original)",
        )
    )
    reason: NotBlankStr = Field(
        description="Why the retry is needed",
    )
