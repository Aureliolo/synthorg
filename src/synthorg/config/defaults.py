"""Built-in default values for company configuration."""


def default_config_dict() -> dict[str, object]:
    """Return base-layer configuration defaults as a raw dict.

    These defaults serve as the base layer; user-provided YAML values
    override them during merging.  They ensure that every field has a
    sensible starting value even if omitted from the config file.

    Returns:
        Base-layer configuration dictionary.
    """
    return {
        "company_name": "SynthOrg",
        "company_type": "custom",
        "departments": [],
        "agents": [],
        "custom_roles": [],
        "config": {},
        "budget": {},
        "communication": {},
        "providers": {},
        "routing": {},
        "logging": None,
        "graceful_shutdown": {},
        "workflow_handoffs": [],
        "escalation_paths": [],
        "coordination_metrics": {},
        "task_assignment": {},
        "memory": {},
        "persistence": {},
        "cost_tiers": {},
        "org_memory": {},
        "api": {},
        "sandboxing": {},
        "mcp": {},
        "security": {},
        "trust": {},
        "promotion": {},
        "task_engine": {},
        "coordination": {},
    }
