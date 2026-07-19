# module-kind: service
"""Output-style policy service: the single hard-layer consumption point.

Holds the active pack, operator config, compiled evaluator, and merged
exemptions, and exposes a synchronous ``evaluate`` for every agent-output
boundary (deliverable completion gate, inter-agent messages, commit messages,
PR/issue bodies). The service is hot-swappable: the settings subscriber rebuilds
it and re-binds the ambient reference on a config change, so a policy change
takes effect on the next boundary check without a restart.

A process-global ambient reference lets the boundaries (which live across the
communication, tools, and engine layers) reach the service without threading it
through every signature, mirroring the strategy module's ambient providers. The
service also builds the soft-layer house-style provider from the same pack, so
both layers stay driven by one pluggable pack.
"""

from synthorg.engine.output_style.evaluator import OutputPolicyEvaluator
from synthorg.engine.output_style.exemptions import OutputContext
from synthorg.engine.output_style.models import (
    HouseStyleDirective,
    OutputChannel,
    OutputPolicyVerdict,
    OutputStyleConfig,
    RulePack,
)
from synthorg.engine.output_style.pack_loader import load_pack, merge_exemptions
from synthorg.engine.output_style.provider import SnapshotHouseStyleProvider


class OutputStylePolicyService:
    """Holds the active pack + config and evaluates output at boundaries."""

    def __init__(self, *, pack: RulePack, config: OutputStyleConfig) -> None:
        """Build the compiled evaluator from the pack and operator config.

        Args:
            pack: The active rule pack (hard rules + soft directives).
            config: Operator configuration (enabled, shadow, exemptions).

        Raises:
            OutputStylePackValidationError: If a rule's regex fails to compile.
        """
        self._pack = pack
        self._config = config
        self._evaluator = OutputPolicyEvaluator(
            rules=pack.rules,
            exemptions=merge_exemptions(pack, config),
            shadow_mode=config.shadow_mode,
        )

    @classmethod
    def from_config(cls, config: OutputStyleConfig) -> OutputStylePolicyService:
        """Build a service by loading the pack named in *config*.

        Returns:
            A fully constructed service.

        Raises:
            OutputStylePackNotFoundError: If the configured pack is unknown.
            OutputStylePackValidationError: If the pack fails validation.
        """
        return cls(pack=load_pack(config.pack), config=config)

    @property
    def enabled(self) -> bool:
        """Whether the hard guardrail is active."""
        return self._config.enabled

    @property
    def config(self) -> OutputStyleConfig:
        """The operator configuration in effect."""
        return self._config

    @property
    def pack(self) -> RulePack:
        """The active rule pack."""
        return self._pack

    def evaluate(self, text: str, ctx: OutputContext) -> OutputPolicyVerdict:
        """Evaluate agent output at a boundary.

        Args:
            text: The agent-produced output.
            ctx: The output context.

        Returns:
            A clean pass-through verdict when the policy is disabled; otherwise
            the evaluator's verdict.
        """
        if not self._config.enabled:
            return OutputPolicyVerdict(channel=ctx.channel)
        return self._evaluator.evaluate(text, ctx)

    def house_style_directives(self) -> tuple[HouseStyleDirective, ...]:
        """Return the pack's soft directives (empty when the soft layer is off).

        Returns:
            The house-style directives, or an empty tuple when disabled.
        """
        if not self._config.house_style_enabled:
            return ()
        return self._pack.house_style

    def build_house_style_provider(self) -> SnapshotHouseStyleProvider:
        """Build the soft-layer provider from this service's pack + config.

        Returns:
            A provider serving the pack's directives, disabled when the soft
            layer is switched off.
        """
        return SnapshotHouseStyleProvider(
            self._pack.house_style, enabled=self._config.house_style_enabled
        )


_AMBIENT_SERVICE: OutputStylePolicyService | None = None


def set_output_policy_service(service: OutputStylePolicyService | None) -> None:
    """Set the process-global ambient output-policy service.

    Called at boot and by the settings subscriber on a hot-reload. Tests reset
    to ``None`` to restore isolation.
    """
    global _AMBIENT_SERVICE  # noqa: PLW0603 -- single process-wide org policy
    _AMBIENT_SERVICE = service


def current_output_policy_service() -> OutputStylePolicyService | None:
    """Return the ambient output-policy service, or ``None`` when unset.

    Returns:
        The bound service, or ``None`` (persistence-less / pre-wiring boot).
    """
    return _AMBIENT_SERVICE


def output_policy_active() -> bool:
    """Whether an enabled output-policy service is bound.

    Lets a caller decide to retrieve a deliverable for the completion backstop
    without importing the service internals: ``True`` only when the ambient
    service is set and enabled, so the hard guardrail would actually evaluate.

    Returns:
        ``True`` when the ambient service is bound and enabled, else ``False``.
    """
    return _AMBIENT_SERVICE is not None and _AMBIENT_SERVICE.enabled


__all__ = [
    "OutputChannel",
    "OutputStylePolicyService",
    "current_output_policy_service",
    "output_policy_active",
    "set_output_policy_service",
]
