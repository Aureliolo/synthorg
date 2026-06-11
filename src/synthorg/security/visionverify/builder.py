"""Boot wiring for the vision verifier gate.

Builds the :class:`VisionVerifierGate` from a :class:`VisionVerifyConfig`,
returning ``None`` when the subsystem is disabled so the
ReviewGateService short-circuits as if the gate were absent.
"""

from collections.abc import Callable
from pathlib import Path

from synthorg.budget.tracker import CostTracker
from synthorg.core.clock import Clock
from synthorg.core.types import ModelTier
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.visionverify.config import VisionVerifyConfig
from synthorg.security.visionverify.factory import build_vision_verifier
from synthorg.security.visionverify.gate import VisionVerifierGateService
from synthorg.security.visionverify.protocol import VisionVerifierGate

type TierResolver = Callable[[ModelTier], str | None]


def build_vision_verifier_gate(  # noqa: PLR0913 -- verifier deps are intrinsic
    config: VisionVerifyConfig,
    *,
    workspace: Path,
    provider: CompletionProvider | None = None,
    tier_resolver: TierResolver | None = None,
    cost_tracker: CostTracker | None = None,
    clock: Clock | None = None,
) -> VisionVerifierGate | None:
    """Build the vision gate, or ``None`` when the subsystem is disabled.

    Returns:
        The configured ``VisionVerifierGate``, or ``None`` when vision
        verification is disabled.

    Raises:
        VisionVerifyConfigError: When ``llm_vision`` is selected without
            its required provider / tier resolver.
    """
    verifier = build_vision_verifier(
        config,
        workspace=workspace,
        provider=provider,
        tier_resolver=tier_resolver,
        cost_tracker=cost_tracker,
    )
    if verifier is None:
        return None
    return VisionVerifierGateService(verifier=verifier, clock=clock)
