"""Enumerations for the settings persistence layer."""

from enum import StrEnum


class SettingNamespace(StrEnum):
    """Namespace grouping for settings.

    Each namespace corresponds to a subsystem whose settings
    can be edited at runtime via the settings API.
    """

    API = "api"
    CLIENT = "client"
    COMPANY = "company"
    PROVIDERS = "providers"
    MEMORY = "memory"
    BUDGET = "budget"
    SECURITY = "security"
    COORDINATION = "coordination"
    OBSERVABILITY = "observability"
    BACKUP = "backup"
    ENGINE = "engine"
    COMMUNICATION = "communication"
    A2A = "a2a"
    INTEGRATIONS = "integrations"
    META = "meta"
    NOTIFICATIONS = "notifications"
    SIMULATIONS = "simulations"
    TOOLS = "tools"
    SETTINGS = "settings"
    HR = "hr"
    WORKERS = "workers"
    TELEMETRY = "telemetry"


class SettingType(StrEnum):
    """Data type of a setting value.

    All values are stored as strings in the database; this enum
    drives validation and type coercion in the service layer.
    """

    STRING = "str"
    INTEGER = "int"
    FLOAT = "float"
    BOOLEAN = "bool"
    ENUM = "enum"
    JSON = "json"


class SettingLevel(StrEnum):
    """Visibility level for progressive disclosure in the UI.

    ``BASIC`` settings are shown by default; ``ADVANCED`` settings
    are hidden behind an "Advanced" toggle.
    """

    BASIC = "basic"
    ADVANCED = "advanced"


class SettingSource(StrEnum):
    """Origin of a resolved setting value.

    Listed in descending priority order: database overrides
    take precedence over environment variables, which override
    YAML defaults, which override code defaults.
    """

    DATABASE = "db"
    ENVIRONMENT = "env"
    YAML = "yaml"
    DEFAULT = "default"


class SettingsImportSource(StrEnum):
    """How a settings write entered the service.

    Distinguishes user-driven edits (single-key API set) from bulk
    or programmatic merges so ``SETTINGS_VALIDATION_FAILED`` logs
    can pinpoint whether a malformed value came from a user form, a
    config-file upload, a startup config merge, or a JSON API body.
    """

    DIRECT_SET = "direct_set"
    FILE_UPLOAD = "file_upload"
    CONFIG_MERGE = "config_merge"
    API_BODY = "api_body"
