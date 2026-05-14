"""Bootstrap-resolver-backed defaults for Pydantic settings-mirror fields.

Many Pydantic config classes (``ServerConfig`` style) carry fields that
mirror registered settings. With YAML eliminated from the precedence
chain (RFC #1890), the Pydantic-tier default would otherwise drift from
the env-tier override resolved through ``SettingsService``.

This helper centralises the fix: each Pydantic class with mirror fields
attaches a ``model_validator(mode="before")`` that, for every unset
mirror field, populates the value from
:func:`synthorg.settings.bootstrap_resolver.resolve_init_value`. The
field declarations remain (consumer API unchanged), but the resolved
value at construction time IS the precedence-chain result.

Mirror declarations are stored on the class via the
:class:`MirrorField` dataclass, then collected by
:func:`apply_settings_mirrors` which the validator delegates to.

Explicit caller kwargs always win over registry-resolved values; tests
that pass ``MyConfig(field=X)`` keep getting ``X``.
"""

import json
from collections.abc import Callable  # noqa: TC003
from dataclasses import dataclass
from typing import Any

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace  # noqa: TC001
from synthorg.settings.errors import SettingNotFoundError

_BOOL_TRUE: frozenset[str] = frozenset({"true", "1", "yes"})
_BOOL_FALSE: frozenset[str] = frozenset({"false", "0", "no"})


def parse_bool(raw: str) -> bool | None:
    """Parse a boolean env token (``true``/``false``/``1``/``0``/``yes``/``no``)."""
    token = normalize_ascii_lowercase(raw)
    if token in _BOOL_TRUE:
        return True
    if token in _BOOL_FALSE:
        return False
    return None


def parse_int(raw: str) -> int | None:
    """Parse an integer env token."""
    try:
        return int(raw)
    except ValueError:
        return None


def parse_float(raw: str) -> float | None:
    """Parse a float env token."""
    try:
        return float(raw)
    except ValueError:
        return None


def parse_str_tuple_json(raw: str) -> tuple[str, ...] | None:
    """Parse a JSON list-of-strings env token into a tuple."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    if not all(isinstance(item, str) for item in parsed):
        return None
    return tuple(parsed)


@dataclass(frozen=True)
class MirrorField:
    """Declaration of one settings-mirror Pydantic field.

    Attributes:
        field: Pydantic field name on the owning config class.
        namespace: Registered setting namespace.
        key: Registered setting key.
        parse: Optional value parser; defaults to identity (returns the
            raw env string). Returning ``None`` signals invalid input;
            the registered default is then applied.
    """

    field: str
    namespace: SettingNamespace
    key: str
    parse: Callable[[str], Any] | None = None


def apply_settings_mirrors(
    data: Any,
    mirrors: tuple[MirrorField, ...],
) -> Any:
    """Populate any unset mirror fields in ``data`` from the registry.

    Intended for use inside a Pydantic ``model_validator(mode="before")``::

        @model_validator(mode="before")
        @classmethod
        def _apply_mirrors(cls, data: Any) -> Any:
            return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    Args:
        data: Raw model input (dict or already-validated instance).
        mirrors: Mirror-field declarations for the owning class.

    Returns:
        ``data`` with unset mirror fields populated from the registry.
        Caller-supplied keys are preserved verbatim.
    """
    if not isinstance(data, dict):
        return data
    overrides: dict[str, Any] = {}
    for mirror in mirrors:
        if mirror.field in data:
            continue
        try:
            resolved = resolve_init_value(
                mirror.namespace,
                mirror.key,
                parse=mirror.parse,
            )
        except SettingNotFoundError:
            continue
        overrides[mirror.field] = resolved.value
    if not overrides:
        return data
    return {**data, **overrides}
