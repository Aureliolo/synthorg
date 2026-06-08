# ruff: noqa: TRY004 -- all type-validation paths deliberately raise ValueError
# for a consistent public API contract (callers use `except ValueError`).
"""Field/JSON/level/rotation parsers for the sink config builder.

Extracted from ``sink_config_builder`` so the main module stays within
its size budget. These are internal helpers shared by the builder and
the shipping-sink builders; import them via ``sink_config_builder``.
"""

import json

from synthorg.observability import safe_error_description
from synthorg.observability.config import RotationConfig
from synthorg.observability.enums import LogLevel, RotationStrategy

_LEVEL_MAP: dict[str, LogLevel] = {level.value.lower(): level for level in LogLevel}

_STRATEGY_MAP: dict[str, RotationStrategy] = {
    s.value.lower(): s for s in RotationStrategy
}

_ROTATION_FIELDS: frozenset[str] = frozenset(
    {"strategy", "max_bytes", "backup_count", "compress_rotated"},
)


def reject_unknown_fields(
    fields: dict[str, object],
    allowed: frozenset[str],
    context: str,
) -> None:
    """Raise ValueError if *fields* contains keys not in *allowed*.

    Raises:
        ValueError: If *fields* contains any key not present in *allowed*.
    """
    unknown = set(fields) - allowed
    if unknown:
        msg = f"Unknown fields in {context}: {sorted(unknown)}"
        raise ValueError(msg)


def parse_bool(raw: object, *, field_name: str) -> bool:
    """Require an actual JSON boolean.

    Returns:
        The boolean value of *raw*.

    Raises:
        ValueError: If *raw* is not a ``bool``.
    """
    if not isinstance(raw, bool):
        msg = f"{field_name} must be a boolean, got {type(raw).__name__}"
        raise ValueError(msg)
    return raw


def parse_json(raw: str, label: str) -> object:
    """Parse a JSON string, raising ValueError on failure.

    Returns:
        The deserialized Python object.

    Raises:
        ValueError: If the string is not valid JSON.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON for {label}: {safe_error_description(exc)}"
        raise ValueError(msg) from exc


def parse_level(raw: object) -> LogLevel:
    """Convert a level value to LogLevel (case-insensitive).

    Returns:
        The ``LogLevel`` member matching *raw* (case-insensitive).

    Raises:
        ValueError: If *raw* is not a string or not a recognized level.
    """
    if not isinstance(raw, str):
        msg = f"level must be a string, got {type(raw).__name__}"
        raise ValueError(msg)
    level = _LEVEL_MAP.get(raw.lower())
    if level is None:
        valid = ", ".join(sorted(_LEVEL_MAP))
        msg = f"Invalid level {raw!r}. Valid levels: {valid}"
        raise ValueError(msg)
    return level


def parse_rotation_override(
    raw: object,
    base: RotationConfig | None,
) -> RotationConfig:
    """Merge a rotation override dict into an existing RotationConfig.

    Only fields present in *raw* are overridden; others are preserved
    from *base* (or defaults if base is None).

    Returns:
        A merged ``RotationConfig`` with *raw*'s fields applied over the
        *base* defaults.

    Raises:
        ValueError: If *raw* is not a dict, contains unknown fields,
            or field values are invalid.
    """
    if not isinstance(raw, dict):
        msg = f"rotation must be a JSON object, got {type(raw).__name__}"
        raise ValueError(msg)
    reject_unknown_fields(raw, _ROTATION_FIELDS, "rotation")

    base = base or RotationConfig()
    updates: dict[str, object] = {}

    if "strategy" in raw:
        strategy = _STRATEGY_MAP.get(str(raw["strategy"]).lower())
        if strategy is None:
            valid = ", ".join(sorted(_STRATEGY_MAP))
            msg = f"Invalid rotation strategy {raw['strategy']!r}. Valid: {valid}"
            raise ValueError(msg)
        updates["strategy"] = strategy

    if "max_bytes" in raw:
        val = raw["max_bytes"]
        if not isinstance(val, int) or isinstance(val, bool):
            msg = f"Invalid max_bytes value {val!r}: must be an integer"
            raise ValueError(msg)
        updates["max_bytes"] = val

    if "backup_count" in raw:
        val = raw["backup_count"]
        if not isinstance(val, int) or isinstance(val, bool):
            msg = f"Invalid backup_count value {val!r}: must be an integer"
            raise ValueError(msg)
        updates["backup_count"] = val

    if "compress_rotated" in raw:
        updates["compress_rotated"] = parse_bool(
            raw["compress_rotated"],
            field_name="rotation.compress_rotated",
        )

    if not updates:
        return base
    merged = {**base.model_dump(), **updates}
    # model_validate enforces compress_rotated + strategy check via
    # RotationConfig._reject_compress_with_external.
    return RotationConfig.model_validate(merged)


def parse_enum_field[T](
    entry: dict[str, object],
    key: str,
    mapping: dict[str, T],
    label: str,
    context: str,
) -> T:
    """Parse a string field as an enum via a lookup map.

    Returns:
        The enum member that *entry[key]* maps to (case-insensitive).

    Raises:
        ValueError: If the value is not a string or does not match any
            entry in *mapping*.
    """
    raw = entry[key]
    if not isinstance(raw, str):
        msg = f"{context}.{key} must be a string"
        raise ValueError(msg)
    parsed = mapping.get(raw.lower())
    if parsed is None:
        valid = ", ".join(sorted(mapping))
        msg = f"Invalid {label} {raw!r}. Valid: {valid}"
        raise ValueError(msg)
    return parsed


def parse_common_sink_fields(
    entry: dict[str, object],
    index: int,
    *,
    sink_type: str = "file",
) -> tuple[LogLevel, bool]:
    """Extract level and json_format from a custom sink entry.

    Args:
        entry: The custom sink entry dict.
        index: Index within the custom_sinks array.
        sink_type: The sink type string (file, syslog, http).

    Returns:
        Tuple of (level, json_format).

    Raises:
        ValueError: If json_format is set for syslog/http sinks.
    """
    level = parse_level(entry["level"]) if "level" in entry else LogLevel.INFO
    json_format = True
    if "json_format" in entry:
        if sink_type in ("syslog", "http"):
            msg = (
                f"json_format is not supported for "
                f"{sink_type} sinks (custom_sinks[{index}])"
            )
            raise ValueError(msg)
        json_format = parse_bool(
            entry["json_format"],
            field_name=f"custom_sinks[{index}].json_format",
        )
    return level, json_format


def parse_int_field(
    entry: dict[str, object],
    key: str,
    context: str,
) -> int:
    """Parse a strict integer field (rejects booleans).

    Returns:
        The integer value of *entry[key]*.

    Raises:
        ValueError: If the value is not a plain ``int`` (a ``bool`` is
            rejected).
    """
    val = entry[key]
    if not isinstance(val, int) or isinstance(val, bool):
        msg = f"{context}.{key} must be an integer"
        raise ValueError(msg)
    return val


def parse_number_field(
    entry: dict[str, object],
    key: str,
    context: str,
) -> float:
    """Parse a numeric field (int or float, rejects booleans).

    Returns:
        The value of *entry[key]* coerced to ``float``.

    Raises:
        ValueError: If the value is not ``int`` or ``float`` (a ``bool``
            is rejected).
    """
    val = entry[key]
    if not isinstance(val, int | float) or isinstance(val, bool):
        msg = f"{context}.{key} must be a number"
        raise ValueError(msg)
    return float(val)
