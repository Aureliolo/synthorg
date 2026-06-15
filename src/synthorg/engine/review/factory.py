# module-kind: code
"""Factory for assembling the review pipeline from a strategy discriminator.

The shipped default (``internal_only``) reproduces the historical
boot-time construction exactly: a single :class:`InternalReviewStage`.
The ``client_then_internal`` strategy prepends a
:class:`ClientReviewStage` that delegates to a pool of synthetic
clients before the internal gate runs, but only when a non-empty client
pool and a pool strategy are supplied; absent either, it degrades to
internal-only so a misconfigured boot can never brick the review path.
"""

from typing import Literal, assert_never

from synthorg.client.protocols import ClientInterface, ClientPoolStrategy
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review.protocol import ReviewStage
from synthorg.engine.review.stages.client import ClientReviewStage
from synthorg.engine.review.stages.internal import InternalReviewStage
from synthorg.observability import get_logger
from synthorg.observability.events.review_pipeline import REVIEW_PIPELINE_BUILT

logger = get_logger(__name__)

ReviewPipelineStrategy = Literal["internal_only", "client_then_internal"]


def build_review_pipeline(
    *,
    strategy: ReviewPipelineStrategy = "internal_only",
    client_pool: tuple[ClientInterface, ...] = (),
    pool_strategy: ClientPoolStrategy | None = None,
) -> ReviewPipeline:
    """Build the review pipeline selected by *strategy*.

    Args:
        strategy: Pipeline discriminator. ``internal_only`` (default)
            yields a single internal stage; ``client_then_internal``
            prepends a client-delegated stage when a pool is available.
        client_pool: Candidate clients for the client stage. An empty
            pool forces internal-only regardless of *strategy*.
        pool_strategy: Selection strategy for the client stage. Required
            for the client stage; its absence forces internal-only.

    Returns:
        A :class:`ReviewPipeline` whose stages match the resolved
        strategy.
    """
    stages: list[ReviewStage] = []
    if strategy == "internal_only":
        client_stage_active = False
    elif strategy == "client_then_internal":
        if client_pool and pool_strategy is not None:
            stages.append(ClientReviewStage(pool=client_pool, strategy=pool_strategy))
            client_stage_active = True
        else:
            client_stage_active = False
            logger.warning(
                REVIEW_PIPELINE_BUILT,
                strategy=strategy,
                note="client stage requested but degraded to internal-only",
                has_pool=bool(client_pool),
                has_pool_strategy=pool_strategy is not None,
            )
    else:  # pragma: no cover
        assert_never(strategy)
    stages.append(InternalReviewStage())
    pipeline = ReviewPipeline(stages=tuple(stages))
    logger.info(
        REVIEW_PIPELINE_BUILT,
        strategy=strategy,
        client_stage_active=client_stage_active,
        stages=list(pipeline.stage_names),
    )
    return pipeline
