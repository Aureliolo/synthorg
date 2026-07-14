"""Autonomy resolver -- three-level chain and category expansion."""

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.observability import get_logger
from synthorg.observability.events.autonomy import (
    AUTONOMY_PRESET_EXPANDED,
    AUTONOMY_RESOLVED,
)
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.models import AutonomyConfig, AutonomyPreset

logger = get_logger(__name__)


class AutonomyResolver:
    """Resolves effective autonomy via a three-level chain.

    Resolution order (most specific wins):
    1. Agent-level override
    2. Department-level override
    3. Company-level default

    After resolution, category shortcuts (e.g. ``"code"``) are expanded
    into concrete action types via the ``ActionTypeRegistry``, and the
    ``"all"`` shortcut is expanded to every registered action type.
    """

    def __init__(
        self,
        *,
        registry: ActionTypeRegistry,
        config: AutonomyConfig,
    ) -> None:
        """Initialize the resolver.

        Args:
            registry: Action type registry for category expansion.
            config: Company-level autonomy configuration with presets.
        """
        self._registry = registry
        self._config = config

    def resolve(
        self,
        agent_level: AutonomyLevel | None = None,
        department_level: AutonomyLevel | None = None,
    ) -> EffectiveAutonomy:
        """Resolve effective autonomy from the three-level chain.

        Args:
            agent_level: Per-agent override (highest priority).
            department_level: Per-department override.

        Returns:
            Fully expanded :class:`EffectiveAutonomy`.

        Raises:
            ValueError: If the resolved level has no matching preset.
        """
        level = agent_level or department_level or self._config.level
        preset = self._resolve_preset(level)

        auto_approve = self._expand_patterns(preset.auto_approve)
        human_approval = self._expand_patterns(preset.human_approval)

        result = EffectiveAutonomy(
            level=level,
            auto_approve_actions=auto_approve,
            human_approval_actions=human_approval,
            security_agent=preset.security_agent,
        )

        logger.info(
            AUTONOMY_RESOLVED,
            resolved_level=level.value,
            agent_override=agent_level.value if agent_level else None,
            department_override=department_level.value if department_level else None,
            auto_approve_count=len(auto_approve),
            human_approval_count=len(human_approval),
        )
        return result

    def _resolve_preset(self, level: AutonomyLevel) -> AutonomyPreset:
        """Return the preset for *level*, raising when none is registered.

        Returns:
            The matching :class:`AutonomyPreset`.

        Raises:
            ValueError: If the resolved level has no matching preset.
        """
        preset = self._config.presets.get(level)
        if preset is None:
            msg = (
                f"No preset found for autonomy level {level!r} "
                f"(available: {sorted(self._config.presets)})"
            )
            logger.warning(
                AUTONOMY_RESOLVED,
                resolved_level=level.value if hasattr(level, "value") else str(level),
                error=msg,
            )
            raise ValueError(msg)
        return preset

    def _expand_patterns(
        self,
        patterns: tuple[str, ...],
    ) -> frozenset[str]:
        """Expand category shortcuts and ``"all"`` into concrete types.

        Args:
            patterns: Action type patterns from a preset. Each entry
                can be a concrete type (``"code:read"``), a category
                shortcut (``"code"``), or the literal ``"all"``.

        Returns:
            Frozenset of expanded, concrete action type strings.
        """
        if not patterns:
            return frozenset()

        result: set[str] = set()

        for pattern in patterns:
            if pattern == "all":
                expanded = self._registry.all_types()
                result.update(expanded)
                logger.debug(
                    AUTONOMY_PRESET_EXPANDED,
                    pattern=pattern,
                    expanded_count=len(expanded),
                )
                continue

            # Try category expansion first.
            category_types = self._registry.expand_category(pattern)
            if category_types:
                result.update(category_types)
                logger.debug(
                    AUTONOMY_PRESET_EXPANDED,
                    pattern=pattern,
                    expanded_count=len(category_types),
                )
                continue

            # Treat as a concrete action type.
            if self._registry.is_registered(pattern):
                result.add(pattern)
            else:
                logger.warning(
                    AUTONOMY_PRESET_EXPANDED,
                    pattern=pattern,
                    note=(
                        "pattern not currently registered -- included for "
                        "forward compatibility, verify this is not a typo"
                    ),
                )
                result.add(pattern)

        return frozenset(result)
