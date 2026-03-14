"""Per-domain event name constants for observability.

All event names follow a dotted ``domain.subject[.qualifier]`` convention and are
used as the first positional argument to structured log calls::

    from synthorg.observability.events.config import CONFIG_LOADED

    logger.info(CONFIG_LOADED, config_path=path)

Import constants from their domain module directly (e.g.
``events.provider``, ``events.budget``, ``events.tool``).
"""

__all__: list[str] = []
