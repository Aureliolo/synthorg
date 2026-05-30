"""Hybrid AI+human client implementation."""

from collections.abc import Callable
from typing import Final, Literal

from synthorg.client.ai_client import AIClient
from synthorg.client.human_client import HumanClient
from synthorg.client.models import (
    ClientFeedback,
    ClientProfile,
    GenerationContext,
    ReviewContext,
    TaskRequirement,
)
from synthorg.observability import get_logger

logger = get_logger(__name__)


HybridRouter = Callable[
    [ClientProfile, GenerationContext | ReviewContext],
    Literal["ai", "human"],
]


_HUMAN_ROUTING_THRESHOLD: Final[float] = 0.8


def default_router(
    profile: ClientProfile,
    context: GenerationContext | ReviewContext,
) -> Literal["ai", "human"]:
    """Route based on ``strictness_level``: >=0.8 goes to human.

    Strict personas lean on humans, lenient personas lean on the
    AI delegate. This is a sensible baseline; callers can inject a
    different router at construction time.

    Returns:
        ``"human"`` when ``strictness_level`` meets the threshold, else
        ``"ai"``.
    """
    del context
    if profile.strictness_level >= _HUMAN_ROUTING_THRESHOLD:
        return "human"
    return "ai"


class HybridClient:
    """Composes an AI client and a human client via a router.

    Each incoming request is dispatched to exactly one of the two
    sub-clients based on the configured router function. Use
    :func:`default_router` for strictness-based routing, or inject
    a custom callable for domain- or complexity-driven rules.
    """

    def __init__(
        self,
        *,
        profile: ClientProfile,
        ai_client: AIClient,
        human_client: HumanClient,
        router: HybridRouter = default_router,
    ) -> None:
        """Initialize the hybrid client.

        Args:
            profile: Profile describing the composite client.
            ai_client: Delegate for AI-routed requests.
            human_client: Delegate for human-routed requests.
            router: Callable selecting ``"ai"`` or ``"human"``.
        """
        self._profile = profile
        self._ai = ai_client
        self._human = human_client
        self._router = router

    @property
    def profile(self) -> ClientProfile:
        """Expose the client profile for pool/selector consumption."""
        return self._profile

    def _resolve_delegate(
        self,
        context: GenerationContext | ReviewContext,
    ) -> AIClient | HumanClient:
        route = self._router(self._profile, context)
        if route not in {"ai", "human"}:
            msg = f"Unsupported route {route!r} from hybrid router"
            raise ValueError(msg)
        return self._human if route == "human" else self._ai

    async def submit_requirement(
        self,
        context: GenerationContext,
    ) -> TaskRequirement | None:
        """Route submission through AI or human per the router.

        Returns:
            The requirement produced by the routed delegate, or ``None``.
        """
        delegate = self._resolve_delegate(context)
        return await delegate.submit_requirement(context)

    async def review_deliverable(
        self,
        context: ReviewContext,
    ) -> ClientFeedback:
        """Route review through AI or human per the router.

        Returns:
            The feedback produced by the routed delegate.
        """
        delegate = self._resolve_delegate(context)
        return await delegate.review_deliverable(context)
