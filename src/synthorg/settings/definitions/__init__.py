"""Setting definitions for all namespaces.

Importing this package triggers registration of all setting
definitions into the global :func:`~synthorg.settings.registry.get_registry`.
"""

from synthorg.settings.definitions import (
    a2a,
    api,
    backup,
    budget,
    client,
    communication,
    company,
    coordination,
    engine,
    hr,
    integrations,
    memory,
    meta,
    notifications,
    objectives,
    observability,
    providers,
    security,
    settings_ns,
    simulations,
    telemetry,
    tools,
    workers,
)

__all__ = [
    "a2a",
    "api",
    "backup",
    "budget",
    "client",
    "communication",
    "company",
    "coordination",
    "engine",
    "hr",
    "integrations",
    "memory",
    "meta",
    "notifications",
    "objectives",
    "observability",
    "providers",
    "security",
    "settings_ns",
    "simulations",
    "telemetry",
    "tools",
    "workers",
]
