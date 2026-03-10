"""Autonomy resolver — three-level chain and category expansion."""

from ai_company.core.enums import AutonomyLevel, SeniorityLevel, compare_seniority
from ai_company.observability import get_logger
from ai_company.observability.events.autonomy import (
    AUTONOMY_PRESET_EXPANDED,
    AUTONOMY_RESOLVED,
    AUTONOMY_SENIORITY_VIOLATION,
)
from ai_company.security.action_types import ActionTypeRegistry  # noqa: TC001
from ai_company.security.autonomy.models import (
    AutonomyConfig,
    EffectiveAutonomy,
)

logger = get_logger(__name__)

# Seniority threshold: JUNIOR agents cannot have FULL autonomy.
_JUNIOR_MAX_AUTONOMY = AutonomyLevel.SEMI


class AutonomyResolver:
    """Resolves effective autonomy via a three-level chain.

    Resolution order (most specific wins):
    1. Agent-level override
    2. Department-level override
    3. Company-level default

    After resolution, category shortcuts (e.g. ``"code"``) are expanded
    into concrete action types via the ``ActionTypeRegistry``, and the
    ``"all"`` shortcut is expanded to every registered action type.

    Args:
        registry: Action type registry for category expansion.
        config: Company-level autonomy configuration with presets.
    """

    def __init__(
        self,
        *,
        registry: ActionTypeRegistry,
        config: AutonomyConfig,
    ) -> None:
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
        preset = self._config.presets.get(level)
        if preset is None:
            msg = (
                f"No preset found for autonomy level {level!r} "
                f"(available: {sorted(self._config.presets)})"
            )
            raise ValueError(msg)

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

    def validate_seniority(
        self,
        seniority: SeniorityLevel,
        autonomy: AutonomyLevel,
    ) -> None:
        """Reject JUNIOR agents with FULL autonomy (D6).

        Args:
            seniority: The agent's seniority level.
            autonomy: The requested autonomy level.

        Raises:
            ValueError: If a JUNIOR agent requests FULL autonomy.
        """
        if (
            compare_seniority(seniority, SeniorityLevel.JUNIOR) <= 0
            and autonomy == AutonomyLevel.FULL
        ):
            logger.warning(
                AUTONOMY_SENIORITY_VIOLATION,
                seniority=seniority.value,
                autonomy=autonomy.value,
            )
            msg = (
                f"Seniority level {seniority.value!r} cannot have "
                f"FULL autonomy — maximum is {_JUNIOR_MAX_AUTONOMY.value!r}"
            )
            raise ValueError(msg)

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
                # Unknown pattern — still include it so the security
                # layer can match it.  Custom action types registered
                # later may use this pattern.
                result.add(pattern)

        return frozenset(result)
