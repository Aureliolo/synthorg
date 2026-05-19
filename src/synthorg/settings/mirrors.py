"""Bootstrap-resolver-backed defaults for Pydantic settings-mirror fields.

Many Pydantic config classes (``ServerConfig`` style) carry fields that
mirror registered settings. Without an active sync, the Pydantic-tier
default would drift from the env-tier override resolved through
``SettingsService``.

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
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.errors import SettingNotFoundError

_BOOL_TRUE: frozenset[str] = frozenset({"true", "1", "yes"})
_BOOL_FALSE: frozenset[str] = frozenset({"false", "0", "no"})


def parse_bool(raw: str) -> bool | None:
    """Parse a boolean env token.

    Accepts ``true``, ``false``, ``1``, ``0``, ``yes``, ``no`` (case-insensitive).
    """
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


def resolve_init_int(namespace: SettingNamespace, key: str) -> int:
    """Resolve an integer-typed setting at app construction time.

    Cat-2 boot knob: the consumer is built before the
    ``SettingsService`` connects, so the value is sourced env >
    registered default via the bootstrap resolver (a runtime change
    requires a restart). ``parse_int`` makes a non-integer env value
    fall through to the registered default rather than raising at
    construction time. This is the single sanctioned int-resolver so
    boot sites do not each re-implement ``resolve_init_value`` +
    ``parse_int`` + ``int(...)``.
    """
    resolved = resolve_init_value(namespace, key, parse=parse_int)
    return int(resolved.value)


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


def parse_json_int_pair_dict(raw: str) -> dict[str, list[int]] | None:
    """Parse a JSON ``{op_name: [int, int]}`` env token.

    Returns the raw ``dict[str, list[int]]`` produced by ``json.loads``;
    the owning Pydantic config's ``mode="before"`` validator promotes
    inner lists to tuples and rejects malformed values, so this parser
    only enforces top-level JSON shape (object with string keys).
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if not all(isinstance(k, str) for k in parsed):
        return None
    return parsed


def parse_json_int_dict(raw: str) -> dict[str, int] | None:
    """Parse a JSON ``{op_name: int}`` env token.

    Returns the raw ``dict[str, int]`` from ``json.loads``. The owning
    Pydantic validator rejects non-int and negative values, so this
    parser only enforces top-level JSON shape (object with string keys).
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if not all(isinstance(k, str) for k in parsed):
        return None
    return parsed


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
        only_if_env_set: When ``True`` the mirror fires ONLY when the
            operator has explicitly set the env var; if the resolver
            falls back to the registered default, the Pydantic field
            keeps its declared default. Use this when the Pydantic
            field's default sentinel carries meaning the registry
            default would overwrite (``None`` = "inherit from parent"
            on CeremonyPolicyConfig, ``None`` = "unlimited" on
            ``CoordinationSectionConfig.max_concurrency_per_wave``,
            ``None`` = "auto-derive from API prefix" on
            ``AuthConfig.exclude_paths``).
    """

    field: str
    namespace: SettingNamespace
    key: str
    parse: Callable[[str], Any] | None = None
    only_if_env_set: bool = False


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
        Caller-supplied keys are preserved verbatim. Mirrors declared
        with ``only_if_env_set=True`` apply only when an env override
        is present; the Pydantic default sentinel survives otherwise.
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
        if mirror.only_if_env_set and resolved.source != SettingSource.ENVIRONMENT:
            continue
        overrides[mirror.field] = resolved.value
    if not overrides:
        return data
    return {**data, **overrides}
