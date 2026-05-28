"""Semantic drift detector middleware.

Compares model output against task acceptance criteria using cosine
similarity.  Fail-soft: logs a warning and annotates context metadata
but never blocks execution.

Opt-in: registered in ``_AGENT_OPT_IN``, must be added to the
middleware chain explicitly.
"""

import threading
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.text_similarity import cosine_word_similarity
from synthorg.core.types import NotBlankStr
from synthorg.engine.middleware.protocol import BaseAgentMiddleware, ModelCallable
from synthorg.observability import get_logger
from synthorg.observability.events.middleware import (
    MIDDLEWARE_SEMANTIC_DRIFT_DETECTED,
    MIDDLEWARE_SEMANTIC_DRIFT_ERROR,
    MIDDLEWARE_SEMANTIC_DRIFT_SKIPPED,
)

if TYPE_CHECKING:
    from synthorg.engine.middleware.models import (
        AgentMiddlewareContext,
        ModelCallResult,
    )

logger = get_logger(__name__)

_MAX_SKIPPED_LOGGED: Final[int] = 1024
_SKIPPED_LOGGED: dict[str, None] = {}
_SKIPPED_LOCK = threading.Lock()


class SemanticDriftConfig(BaseModel):
    """Configuration for the semantic drift detector.

    Attributes:
        enabled: Whether drift detection is active.
        threshold: Cosine similarity below which drift is flagged.
        embedding_model: Optional model name for embeddings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether drift detection is active",
    )
    threshold: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Similarity threshold below which drift is flagged",
    )
    embedding_model: NotBlankStr | None = Field(
        default=None,
        description="Optional embedding model name",
    )


class SemanticDriftDetector(BaseAgentMiddleware):
    """Detect semantic drift between model output and task criteria.

    Runs in the ``wrap_model_call`` slot.  Calls the model first,
    then compares the output's semantic similarity to the task's
    acceptance criteria.  If similarity is below threshold, logs
    a warning and annotates the context metadata.

    Never blocks execution -- fail-soft on all errors.

    Args:
        config: Drift detection configuration.
    """

    def __init__(
        self,
        *,
        config: SemanticDriftConfig | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="semantic_drift_detector")
        self._config = config or SemanticDriftConfig()

    async def wrap_model_call(
        self,
        ctx: AgentMiddlewareContext,
        call: ModelCallable,
    ) -> ModelCallResult:
        """Call model, then check for semantic drift.

        Args:
            ctx: Middleware context.
            call: Inner model call.

        Returns:
            Model call result (never modified).
        """
        result = await call(ctx)

        if not self._config.enabled:
            return result

        # Extract acceptance criteria from task.
        criteria = getattr(ctx.task, "acceptance_criteria", None)
        if not criteria:
            task_key = str(ctx.task_id)
            with _SKIPPED_LOCK:
                already_logged = task_key in _SKIPPED_LOGGED
                if not already_logged:
                    _SKIPPED_LOGGED[task_key] = None
                    # Evict oldest entries when cache is full.
                    while len(_SKIPPED_LOGGED) > _MAX_SKIPPED_LOGGED:
                        _SKIPPED_LOGGED.pop(next(iter(_SKIPPED_LOGGED)))
            if not already_logged:
                logger.debug(
                    MIDDLEWARE_SEMANTIC_DRIFT_SKIPPED,
                    task_id=str(ctx.task_id),
                    reason="acceptance_criteria_missing",
                )
            return result

        try:
            similarity = await self._compute_similarity(
                str(result.response_text),
                str(criteria),
            )

            if similarity < self._config.threshold:
                logger.warning(
                    MIDDLEWARE_SEMANTIC_DRIFT_DETECTED,
                    agent_id=str(ctx.agent_id),
                    task_id=str(ctx.task_id),
                    similarity=similarity,
                    threshold=self._config.threshold,
                )
                ctx = ctx.with_metadata("semantic_drift_score", similarity)

        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MIDDLEWARE_SEMANTIC_DRIFT_ERROR,
                agent_id=str(ctx.agent_id),
                task_id=str(ctx.task_id),
            )

        return result

    async def _compute_similarity(
        self,
        text_a: str,
        text_b: str,
    ) -> float:
        """Compute cosine similarity between two texts.

        Uses a simple token-overlap heuristic as the default
        implementation.  Production deployments should override this
        with an embedding-based similarity via the provider API.

        Args:
            text_a: First text.
            text_b: Second text.

        Returns:
            Similarity score in [0.0, 1.0].
        """
        return cosine_word_similarity(text_a, text_b)
