"""Config bridge -- extract setting values from RootConfig by dotted path.

Maps ``(namespace, key)`` pairs to dotted attribute paths in
``RootConfig`` for YAML-layer resolution in the settings service.
"""

import json

from pydantic import BaseModel

from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_CONFIG_PATH_MISS

logger = get_logger(__name__)


def _to_json_compatible(value: object) -> object:
    """Recursively convert Pydantic models to JSON-compatible dicts.

    Walks nested structures so that ``BaseModel`` instances at any
    depth are replaced by their ``model_dump(mode="json")`` output.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (tuple, list)):
        return [_to_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {k: _to_json_compatible(v) for k, v in value.items()}
    return value


def _serialize_value(value: object) -> str:
    """Serialize a resolved config value to a string.

    Handles Pydantic models, tuples/lists, and dicts (including
    nested models at any depth) by producing valid JSON.  Scalar
    booleans produce lowercase JSON-style ``"true"``/``"false"``.
    Other accepted scalars (``str``, ``int``, ``float``) use
    ``str()``.

    Args:
        value: The resolved config attribute.

    Returns:
        A string representation suitable for the settings layer.

    Raises:
        TypeError: If *value* is not an accepted type (accepted:
            ``BaseModel``, ``tuple``, ``list``, ``dict``, ``str``,
            ``int``, ``float``, ``bool``).
    """
    if isinstance(value, BaseModel):
        return json.dumps(value.model_dump(mode="json"))

    if isinstance(value, (tuple, list)):
        return json.dumps(_to_json_compatible(value))

    if isinstance(value, dict):
        return json.dumps(_to_json_compatible(value))

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, (str, int, float)):
        return str(value)

    msg = f"Cannot serialize {type(value).__name__} to settings string"
    raise TypeError(msg)


def extract_from_config(config: object, yaml_path: str) -> str | None:
    """Resolve a dotted path against a config object.

    Traverses the object attribute chain for each segment in
    *yaml_path*.  Returns a serialized string if the final
    attribute exists and is not ``None``, otherwise ``None``.

    For Pydantic models, tuples/lists containing models, and
    dicts with model values, the result is valid JSON.  Scalar
    booleans produce lowercase ``"true"``/``"false"``.  Other
    scalars (``str``, ``int``, ``float``) use ``str(value)``.

    Args:
        config: Root config object (typically ``RootConfig``).
        yaml_path: Dot-separated attribute path
            (e.g. ``"budget.total_monthly"``).

    Returns:
        The resolved value as a string, or ``None`` if the path
        cannot be resolved.
    """
    current: object = config
    for segment in yaml_path.split("."):
        try:
            current = getattr(current, segment)
        except AttributeError:
            logger.debug(
                SETTINGS_CONFIG_PATH_MISS,
                yaml_path=yaml_path,
                failed_segment=segment,
            )
            return None
        if current is None:
            return None
    try:
        return _serialize_value(current)
    except TypeError:
        logger.warning(
            SETTINGS_CONFIG_PATH_MISS,
            yaml_path=yaml_path,
            reason="unsupported_type",
            value_type=type(current).__name__,
        )
        raise
