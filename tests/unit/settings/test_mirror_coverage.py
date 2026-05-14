"""Regression coverage for `_MIRROR_FIELDS` and bridge-default alignment.

Two parametrised gates live in this module:

1. `test_mirror_field_propagates_env_override` walks every
   `_MIRROR_FIELDS` declaration in `synthorg.*`, sets the corresponding
   env var, constructs the owning Pydantic config, and asserts the env
   value reaches the declared field. Discovery is via `pkgutil` +
   `inspect`, so a new mirror class is auto-covered without touching
   this file.

2. `test_bridge_config_default_matches_registered_default` walks every
   bridge-config field assembled by `ConfigResolver.get_*_bridge_config`
   and asserts the Pydantic-tier `Field(default=...)` matches the
   registered `SettingDefinition.default` after type coercion. Permanent
   enforcement gate for the bridge_configs convention: BridgeConfig and
   the registry must never disagree on a default.
"""

import importlib
import inspect
import json
import pkgutil
import re
from typing import Any, Final

import pytest
from annotated_types import Ge, Gt, Le, Lt
from pydantic import BaseModel
from pydantic.fields import FieldInfo

import synthorg
from synthorg.settings import bridge_configs as bridge_configs_module
from synthorg.settings import definitions as _settings_definitions  # noqa: F401
from synthorg.settings.enums import SettingType
from synthorg.settings.mirrors import (
    MirrorField,
    parse_bool,
    parse_float,
    parse_int,
    parse_str_tuple_json,
)
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver

pytestmark = pytest.mark.unit


def _discover_mirror_classes() -> list[tuple[type[BaseModel], MirrorField]]:
    """Walk `synthorg.*` and return every (class, MirrorField) pair.

    Empty `_MIRROR_FIELDS = ()` declarations are skipped: nothing to
    assert there.
    """
    pairs: list[tuple[type[BaseModel], MirrorField]] = []
    for module_info in pkgutil.walk_packages(synthorg.__path__, prefix="synthorg."):
        try:
            module = importlib.import_module(module_info.name)
        except ImportError:
            continue
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module_info.name:
                continue
            mirrors = getattr(cls, "_MIRROR_FIELDS", None)
            if not isinstance(mirrors, tuple) or not mirrors:
                continue
            for mirror in mirrors:
                if not isinstance(mirror, MirrorField):
                    continue
                pairs.append((cls, mirror))
    return pairs


_MIRROR_CASES: Final[list[tuple[type[BaseModel], MirrorField]]] = (
    _discover_mirror_classes()
)


# Classes whose default construction needs explicit kwargs because the
# Pydantic model declares non-default required fields. The kwargs MUST
# NOT touch the mirrored field under test, since the assertion checks
# that the env override (not the kwarg) drove the value.
#
# When a new mirror class is added that cannot be default-constructed,
# the test fails at construction with a clear error; the fix is to add
# an entry here with the minimum required non-mirror kwargs.
_CONSTRUCTION_KWARGS: Final[dict[str, dict[str, Any]]] = {
    "RootConfig": {"company_name": "TestCo"},
}


# Per-(class, field) overrides for env values when the auto-chooser
# cannot pick a valid value. Identity-parse mirrors whose Pydantic
# field carries a length, pattern, or membership constraint that the
# generic "__mirror_regression__" sentinel violates land here. The
# value is still distinct from the registered default.
_ENV_VALUE_OVERRIDES: Final[dict[tuple[str, str], str]] = {
    ("BudgetConfig", "currency"): "GBP",
    ("CompanyMemoryConfig", "backend"): "inmemory",
}


def _env_var_name(definition: Any) -> str:
    """Return the env var name for a registered SettingDefinition."""
    override = getattr(definition, "env_var_override", None)
    if override:
        return str(override)
    namespace = str(definition.namespace).upper()
    key = str(definition.key).upper()
    return f"SYNTHORG_{namespace}_{key}"


def _registered_default_parsed(definition: Any) -> Any:
    """Return the registered default parsed to its semantic type."""
    raw = definition.default
    if raw is None:
        return None
    setting_type = definition.type
    if setting_type == SettingType.BOOLEAN:
        return parse_bool(str(raw))
    if setting_type == SettingType.INTEGER:
        return parse_int(str(raw))
    if setting_type == SettingType.FLOAT:
        return parse_float(str(raw))
    if setting_type == SettingType.JSON:
        return json.loads(str(raw))
    return str(raw)


def _field_numeric_bounds(field_info: FieldInfo) -> tuple[float, float]:
    """Return the Pydantic ge/gt/le/lt bounds on a numeric field.

    Defaults to a wide range when no constraint is declared.
    """
    lo = float("-inf")
    hi = float("inf")
    for meta in field_info.metadata:
        if isinstance(meta, Ge):
            lo = max(lo, float(meta.ge))  # type: ignore[arg-type]
        elif isinstance(meta, Gt):
            bump = 1 if isinstance(meta.gt, int) else 1e-9
            lo = max(lo, float(meta.gt) + bump)  # type: ignore[arg-type]
        elif isinstance(meta, Le):
            hi = min(hi, float(meta.le))  # type: ignore[arg-type]
        elif isinstance(meta, Lt):
            bump = 1 if isinstance(meta.lt, int) else 1e-9
            hi = min(hi, float(meta.lt) - bump)  # type: ignore[arg-type]
    return lo, hi


def _registry_bounds(definition: Any) -> tuple[float, float]:
    """Return the registered min/max for a numeric setting."""
    lo = float("-inf") if definition.min_value is None else float(definition.min_value)
    hi = float("inf") if definition.max_value is None else float(definition.max_value)
    return lo, hi


def _pick_distinct_int(
    default: int,
    lo: float,
    hi: float,
) -> int:
    """Pick an int within [lo, hi] that is different from default.

    Tries default+1, default-1, lo, hi, and a midpoint in turn.
    """
    candidates: list[int] = []
    if default + 1 <= hi:
        candidates.append(default + 1)
    if default - 1 >= lo:
        candidates.append(default - 1)
    if lo > float("-inf"):
        candidates.append(int(lo))
    if hi < float("inf"):
        candidates.append(int(hi))
    if lo > float("-inf") and hi < float("inf"):
        candidates.append(int((lo + hi) / 2))
    for cand in candidates:
        if cand != default and lo <= cand <= hi:
            return cand
    msg = (
        f"No int candidate distinct from default {default} within bounds"
        f" [{lo}, {hi}] was found"
    )
    raise AssertionError(msg)


def _pick_distinct_float(default: float, lo: float, hi: float) -> float:
    """Pick a float within [lo, hi] that is different from default."""
    for delta in (0.5, 0.25, 0.1, 0.01):
        for cand in (default + delta, default - delta):
            if cand != default and lo <= cand <= hi:
                return round(cand, 6)
    if lo > float("-inf") and lo != default:
        return lo
    if hi < float("inf") and hi != default:
        return hi
    msg = (
        f"No float candidate distinct from default {default} within bounds"
        f" [{lo}, {hi}] was found"
    )
    raise AssertionError(msg)


def _pick_distinct_enum(
    definition: Any,
    default_value: Any,
) -> str:
    """Pick an enum value distinct from the registered default."""
    enum_values = getattr(definition, "enum_values", ()) or ()
    for value in enum_values:
        if str(value) != str(default_value):
            return str(value)
    msg = (
        f"Registered enum {definition.namespace}/{definition.key} has no"
        " value distinct from the default; mirror coverage cannot exercise"
        " the env-override branch."
    )
    raise AssertionError(msg)


def _choose_env_value(  # noqa: PLR0911 -- one branch per parser type is clearer than table dispatch
    mirror: MirrorField,
    definition: Any,
    cls: type[BaseModel],
    registered_default_parsed: Any,
) -> str:
    """Pick an env value distinct from the registered default.

    Honours both the registered ``min_value/max_value`` AND the Pydantic
    field's ``ge/gt/le/lt`` constraints so the constructor accepts the
    value. For enum fields, pulls a distinct value from
    ``SettingDefinition.enum_values``.
    """
    parser = mirror.parse
    if parser is parse_bool:
        if registered_default_parsed is True:
            return "false"
        return "true"
    field_info = cls.model_fields[mirror.field]
    if parser is parse_int:
        reg_lo, reg_hi = _registry_bounds(definition)
        pyd_lo, pyd_hi = _field_numeric_bounds(field_info)
        lo = max(reg_lo, pyd_lo)
        hi = min(reg_hi, pyd_hi)
        int_default = (
            int(registered_default_parsed)
            if registered_default_parsed is not None
            else 0
        )
        return str(_pick_distinct_int(int_default, lo, hi))
    if parser is parse_float:
        reg_lo, reg_hi = _registry_bounds(definition)
        pyd_lo, pyd_hi = _field_numeric_bounds(field_info)
        lo = max(reg_lo, pyd_lo)
        hi = min(reg_hi, pyd_hi)
        float_default = (
            float(registered_default_parsed)
            if registered_default_parsed is not None
            else 0.0
        )
        return str(_pick_distinct_float(float_default, lo, hi))
    if parser is parse_str_tuple_json:
        return '["__mirror_alpha__","__mirror_beta__"]'
    parser_name = getattr(parser, "__name__", "")
    if parser_name == "parse_json_int_pair_dict":
        return '{"__mirror_regression__":[7,11]}'
    if parser_name == "parse_json_int_dict":
        return '{"__mirror_regression__":7}'
    if parser is None:
        # Identity-parse mirror: registered as STRING or ENUM.
        if definition.type == SettingType.ENUM:
            return _pick_distinct_enum(definition, registered_default_parsed)
        return "__mirror_regression__"
    msg = (
        f"No env-value chooser for parser {parser_name!r};"
        " extend _choose_env_value in tests/unit/settings/test_mirror_coverage.py"
    )
    raise AssertionError(msg)


def _expected_value(mirror: MirrorField, env_value: str) -> Any:
    """Apply mirror.parse to the env value (identity if no parser)."""
    if mirror.parse is None:
        return env_value
    return mirror.parse(env_value)


def _coerce_for_field(field_value: Any, expected: Any) -> Any:
    """Normalise expected value to match Pydantic's post-validator shape.

    Tuple fields like `csp_docs_external_origins: tuple[NotBlankStr, ...]`
    receive a list from JSON parsing; Pydantic coerces it to a tuple. The
    regression assertion compares against the post-construction value, so
    expected lists are converted to tuples when the field-side value is a
    tuple.
    """
    if isinstance(field_value, tuple) and isinstance(expected, list):
        return tuple(expected)
    if isinstance(field_value, dict) and isinstance(expected, dict):
        # PerOpRateLimitConfig promotes inner list -> tuple in its
        # mode="before" validator; PerOpConcurrencyConfig leaves int
        # values alone. Match the per-key shape from the actual field.
        coerced: dict[Any, Any] = {}
        for k, v in expected.items():
            actual_v = field_value.get(k)
            if isinstance(actual_v, tuple) and isinstance(v, list):
                coerced[k] = tuple(v)
            else:
                coerced[k] = v
        return coerced
    return expected


@pytest.mark.parametrize(
    ("cls", "mirror"),
    _MIRROR_CASES,
    ids=[f"{cls.__name__}.{m.field}" for cls, m in _MIRROR_CASES],
)
def test_mirror_field_propagates_env_override(
    cls: type[BaseModel],
    mirror: MirrorField,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every declared mirror surfaces an env override at construction."""
    registry = get_registry()
    definition = registry.get(str(mirror.namespace), mirror.key)
    assert definition is not None, (
        f"MirrorField on {cls.__name__}.{mirror.field} references"
        f" unregistered setting {mirror.namespace}/{mirror.key}"
    )

    registered_default = _registered_default_parsed(definition)
    override = _ENV_VALUE_OVERRIDES.get((cls.__name__, mirror.field))
    if override is not None:
        env_value = override
    else:
        env_value = _choose_env_value(mirror, definition, cls, registered_default)
    env_name = _env_var_name(definition)
    monkeypatch.setenv(env_name, env_value)

    extra_kwargs = _CONSTRUCTION_KWARGS.get(cls.__name__, {})
    assert mirror.field not in extra_kwargs, (
        f"_CONSTRUCTION_KWARGS for {cls.__name__} must not override the"
        f" mirrored field {mirror.field!r}; the test would shadow the env"
        " override and silently pass."
    )
    instance = cls(**extra_kwargs)
    actual = getattr(instance, mirror.field)
    expected_raw = _expected_value(mirror, env_value)
    expected = _coerce_for_field(actual, expected_raw)
    assert actual == expected, (
        f"{cls.__name__}.{mirror.field}: env {env_name}={env_value!r}"
        f" did not reach the field; got {actual!r} expected {expected!r}"
    )


# ---------------------------------------------------------------------
# Bridge-default gate (item 4 enforcement)
# ---------------------------------------------------------------------


_BRIDGE_FIELDS_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r'_resolve_bridge_fields\(\s*"([^"]+)"\s*,\s*\((.*?)\),\s*\)',
    re.DOTALL,
)
_BRIDGE_FIELD_ENTRY_RE: Final[re.Pattern[str]] = re.compile(
    r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)',
)
_BRIDGE_BUILDER_RE: Final[re.Pattern[str]] = re.compile(
    r"return\s+(\w+BridgeConfig)\(",
)
# Captures explicit constructor remappings such as
# ``poll_timeout_seconds=values["dispatcher_poll_timeout_seconds"]``.
_BRIDGE_CTOR_REMAP_RE: Final[re.Pattern[str]] = re.compile(
    r'(\w+)\s*=\s*values\[\s*"([^"]+)"\s*\]',
)


def _discover_bridge_entries() -> list[tuple[type[BaseModel], str, str, str, str]]:
    """Walk ConfigResolver bridge builders and yield (cls, field, ns, key, type).

    Each `get_*_bridge_config` method follows one of two shapes:

    1. Default: pass-through unpack into BridgeConfig(**values), with
       registered key == BridgeConfig field name.

    2. Remapped: explicit constructor call like
       ``return XBridgeConfig(short_field=values["namespaced_key"], ...)``
       used when the registry key is verbose but the dataclass field
       is short. We detect both shapes.
    """
    entries: list[tuple[type[BaseModel], str, str, str, str]] = []
    for name, method in inspect.getmembers(
        ConfigResolver, predicate=inspect.isfunction
    ):
        if not name.startswith("get_") or not name.endswith("_bridge_config"):
            continue
        source = inspect.getsource(method)
        builder_match = _BRIDGE_BUILDER_RE.search(source)
        call_match = _BRIDGE_FIELDS_CALL_RE.search(source)
        if builder_match is None or call_match is None:
            msg = (
                f"Could not parse bridge builder {name!r}; the"
                " `_resolve_bridge_fields` shape changed and the gate"
                " discovery regex needs updating."
            )
            raise AssertionError(msg)
        cls_name = builder_match.group(1)
        namespace = call_match.group(1)
        body = call_match.group(2)
        cls = getattr(bridge_configs_module, cls_name, None)
        assert cls is not None, (
            f"Bridge builder {name!r} returns {cls_name!r} which is not"
            " importable from synthorg.settings.bridge_configs."
        )
        # Build the registered_key -> field_name map. Default is
        # identity; explicit remappings via ``field=values["key"]``
        # override.
        registered_keys: list[tuple[str, str]] = []
        for field_match in _BRIDGE_FIELD_ENTRY_RE.finditer(body):
            registered_key = field_match.group(1)
            scalar_type = field_match.group(2)
            registered_keys.append((registered_key, scalar_type))
        remap: dict[str, str] = {}
        builder_tail = source[builder_match.end() :]
        for ctor_match in _BRIDGE_CTOR_REMAP_RE.finditer(builder_tail):
            field_name = ctor_match.group(1)
            registered_key = ctor_match.group(2)
            remap[registered_key] = field_name
        for registered_key, scalar_type in registered_keys:
            field_name = remap.get(registered_key, registered_key)
            entries.append((cls, field_name, namespace, registered_key, scalar_type))
    return entries


_BRIDGE_CASES: Final[list[tuple[type[BaseModel], str, str, str, str]]] = (
    _discover_bridge_entries()
)


def _coerce_registered_for_bridge(definition: Any, pydantic_default: Any) -> Any:
    """Coerce the registered default to match the Pydantic-tier shape.

    Mirrors the rules used by `_resolve_bridge_fields`: BOOLEAN -> bool,
    INTEGER -> int, FLOAT -> float, JSON -> parsed; tuple-typed fields
    receive a JSON list whose entries are wrapped to a tuple.
    """
    parsed = _registered_default_parsed(definition)
    if isinstance(pydantic_default, tuple) and isinstance(parsed, list):
        return tuple(parsed)
    return parsed


@pytest.mark.parametrize(
    ("cls", "field_name", "namespace", "registered_key"),
    [(cls, field, ns, key) for cls, field, ns, key, _ in _BRIDGE_CASES],
    ids=[f"{cls.__name__}.{field}" for cls, field, _, _, _ in _BRIDGE_CASES],
)
def test_bridge_config_default_matches_registered_default(
    cls: type[BaseModel],
    field_name: str,
    namespace: str,
    registered_key: str,
) -> None:
    """Every bridge field's Pydantic default agrees with the registered default."""
    registry = get_registry()
    definition = registry.get(namespace, registered_key)
    assert definition is not None, (
        f"{cls.__name__}.{field_name} bridges {namespace}/{registered_key}"
        " but no SettingDefinition is registered."
    )
    assert field_name in cls.model_fields, (
        f"{cls.__name__} has no Pydantic field {field_name!r} but its"
        f" bridge builder references it."
    )
    pydantic_default = cls.model_fields[field_name].default
    expected = _coerce_registered_for_bridge(definition, pydantic_default)
    assert pydantic_default == expected, (
        f"{cls.__name__}.{field_name} Pydantic default {pydantic_default!r}"
        f" does not match registered default {expected!r} for"
        f" {namespace}/{registered_key}"
    )
