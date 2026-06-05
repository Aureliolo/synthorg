"""Agent bootstrap from persisted configuration.

Loads agent configs from the settings-backed ``ConfigResolver``
and registers them as ``AgentIdentity`` instances in the
``AgentRegistryService``.  Designed to be called on app startup
and again after setup completion.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.agent import (
    AgentIdentity,
    MemoryConfig,
    ModelConfig,
    PersonalityConfig,
    ToolPermissions,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.role import Authority
from synthorg.hr.errors import AgentAlreadyRegisteredError
from synthorg.observability import get_logger
from synthorg.observability.events.setup import (
    SETUP_AGENT_BOOTSTRAP_SKIPPED,
    SETUP_AGENTS_BOOTSTRAPPED,
)

if TYPE_CHECKING:
    from synthorg.config.schema import AgentConfig
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


def _build_model_config(config: AgentConfig) -> ModelConfig:
    """Build a ModelConfig from agent config.

    Raises:
        ValueError: When the agent config has no model section.

    Returns:
        ``ModelConfig`` instance.
    """
    if config.model:
        return ModelConfig.model_validate(config.model)
    msg = f"Agent {config.name!r} has no model config -- skipping"
    raise ValueError(msg)


def _identity_from_config(config: AgentConfig) -> AgentIdentity:
    """Convert a persisted AgentConfig to a runtime AgentIdentity.

    Args:
        config: Agent configuration loaded from settings/YAML.

    Returns:
        A fully constructed AgentIdentity.
    """
    return AgentIdentity(
        name=config.name,
        role=config.role,
        department=config.department,
        level=config.level,
        model=_build_model_config(config),
        personality=(
            PersonalityConfig.model_validate(config.personality)
            if config.personality
            else PersonalityConfig()
        ),
        memory=(
            MemoryConfig.model_validate(config.memory)
            if config.memory
            else MemoryConfig()
        ),
        tools=(
            ToolPermissions.model_validate(config.tools)
            if config.tools
            else ToolPermissions()
        ),
        authority=(
            Authority.model_validate(config.authority)
            if config.authority
            else Authority()
        ),
        autonomy_level=config.autonomy_level,
        strategic_output_mode=config.strategic_output_mode,
        # Hiring date is always "today" -- bootstrap represents re-activation
        # into runtime, not re-creation.  AgentConfig does not persist
        # hiring_date.
        hiring_date=datetime.now(UTC).date(),
    )


async def bootstrap_agents(
    config_resolver: ConfigResolver,
    agent_registry: AgentRegistryService,
) -> int:
    """Bootstrap agents from persisted config into the runtime registry.

    Loads agent configurations via *config_resolver* and registers each
    as an ``AgentIdentity`` in *agent_registry*.  Skips agents that are
    already registered (idempotent) or have invalid/broken configs
    (resilient -- one bad config does not abort the loop).

    Args:
        config_resolver: Resolver for persisted settings.
        agent_registry: Runtime agent registry.

    Returns:
        Count of newly registered agents.
    """
    agent_configs = await config_resolver.get_agents()

    if not agent_configs:
        logger.info(SETUP_AGENTS_BOOTSTRAPPED, count=0)
        return 0

    registered = 0

    for config in agent_configs:
        try:
            identity = _identity_from_config(config)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETUP_AGENT_BOOTSTRAP_SKIPPED,
                agent_name=config.name,
                reason="invalid_config",
            )
            continue

        try:
            await agent_registry.register(identity)
            registered += 1
        except AgentAlreadyRegisteredError:
            logger.debug(
                SETUP_AGENT_BOOTSTRAP_SKIPPED,
                agent_name=config.name,
                agent_id=str(identity.id),
                reason="already_registered",
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETUP_AGENT_BOOTSTRAP_SKIPPED,
                agent_name=config.name,
                agent_id=str(identity.id),
                reason="registration_failed",
            )

    logger.info(
        SETUP_AGENTS_BOOTSTRAPPED,
        count=registered,
        total_configs=len(agent_configs),
    )
    return registered
