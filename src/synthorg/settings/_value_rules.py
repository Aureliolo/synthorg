"""Definition-level rules applied to a setting value.

These decide what a value is allowed to be and where it may come from, from
the definition alone. They hold no service state, so they sit beside the
service rather than inside it.
"""

import re
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_VALIDATION_FAILED
from synthorg.settings.enums import SettingsImportSource
from synthorg.settings.errors import SettingReadOnlyError, SettingValidationError
from synthorg.settings.models import SettingDefinition
from synthorg.settings.type_validators import validate_by_type

logger = get_logger(__name__)

SENSITIVE_MASK: Final[str] = "********"


def env_var_name(namespace: str, key: str) -> str:
    """Build env var name: ``SYNTHORG_{NAMESPACE}_{KEY}`` (uppercased).

    Args:
        namespace: Setting namespace.
        key: Setting key within the namespace.

    Returns:
        The environment-variable name with both namespace and key
        uppercased.
    """
    return f"SYNTHORG_{namespace.upper()}_{key.upper()}"


def reject_if_read_only(
    definition: SettingDefinition,
    *,
    action: str,
    import_source: SettingsImportSource | None = None,
) -> None:
    """Raise ``SettingReadOnlyError`` for compose-set settings.

    The registry entry exists for discoverability via the /settings API; the
    value itself came from the deployment when the container was created.
    Mutation surfaces (``set``, ``set_many``, ``delete``,
    ``delete_namespace``) must reject rather than store a value the running
    process will never read.

    ``import_source`` is included in the validation log when supplied
    so the every ``set()`` rejection path carries the same tag the
    happy path emits, keeping the log tagging contract consistent.

    Args:
        definition: The setting's registry definition.
        action: The mutation being attempted, for the audit log.
        import_source: Where the write came from, when known.

    Raises:
        SettingReadOnlyError: If the setting is ``compose_set``.
    """
    if not definition.compose_set:
        return
    payload: dict[str, object] = {
        "namespace": definition.namespace,
        "key": definition.key,
        "reason": "compose_set",
        "action": action,
    }
    if import_source is not None:
        payload["import_source"] = import_source.value
    logger.warning(SETTINGS_VALIDATION_FAILED, **payload)
    msg = (
        f"Setting {definition.namespace}/{definition.key} is set by the"
        f" deployment and cannot be modified at runtime"
        f" (action={action!r}). Change it in the compose file and recreate"
        f" the container."
    )
    raise SettingReadOnlyError(msg)


def validate_value(definition: SettingDefinition, value: str) -> None:
    """Validate a value against its definition.

    For sensitive settings, error messages mask the actual value
    to prevent secret leakage through validation errors.

    Args:
        definition: The setting's registry definition.
        value: The candidate value, serialised as a string.

    Raises:
        SettingValidationError: If validation fails.
    """
    validate_by_type(definition, value)

    if definition.validator_pattern is not None and not re.fullmatch(
        definition.validator_pattern, value
    ):
        display = SENSITIVE_MASK if definition.sensitive else repr(value)
        msg = f"Value {display} does not match pattern {definition.validator_pattern!r}"
        raise SettingValidationError(msg)
