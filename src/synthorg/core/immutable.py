"""Immutability helpers for frozen Pydantic models.

These utilities support the project's immutability convention
documented in CLAUDE.md: mutable ``dict`` fields are deep-copied at
the model construction boundary so that a caller cannot mutate a
frozen model through a retained reference.

``deep_copy_mapping`` handles ``dict`` inputs only.  Frozen Pydantic
models with ``list`` / ``tuple`` / ``set`` fields should prefer the
immutable tuple form (``tuple[...]``) over runtime wrapping -- tuples
need no deep-copy protection because they are already immutable at
the Python level.

``freeze_recursive`` converts nested mutable containers into their
immutable equivalents (``dict`` -> ``MappingProxyType``, ``list`` ->
``tuple``, ``set`` -> ``frozenset``).  Combined with
``deep_copy_mapping`` it blocks post-construction mutation of audit
records, metadata payloads, and other frozen model fields.

Pydantic ``field_validator(mode="before")`` runs before field type
coercion, which is exactly where we want to intercept dict inputs.
"""

import copy
from types import MappingProxyType


def deep_copy_mapping(value: object) -> object:
    """Deep-copy a mapping value, leaving non-mappings untouched.

    Used as a ``field_validator(mode="before")`` body to isolate frozen
    Pydantic models from caller mutation of ``dict`` fields.

    Args:
        value: The raw field value before Pydantic type coercion.

    Returns:
        A deep copy of ``value`` when it is a ``dict``; otherwise the
        original value unchanged (Pydantic's type validation will
        reject bad types downstream).
    """
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return value


def freeze_recursive(value: object) -> object:
    """Recursively convert mutable containers into immutable forms.

    - ``dict`` -> ``MappingProxyType`` (read-only view of a frozen dict)
    - ``list`` -> ``tuple`` with elements recursively frozen
    - ``tuple`` -> ``tuple`` with elements recursively frozen (tuples
      are themselves immutable but their *elements* can still be
      mutable dicts/lists/sets, so we still recurse)
    - ``set``  -> ``frozenset`` with elements recursively frozen
    - anything else is returned unchanged

    The input is typically a deep copy produced by
    ``deep_copy_mapping``, so nested containers can be freely
    transformed in place before being re-wrapped for the frozen
    Pydantic model field.

    Args:
        value: The value to recursively freeze.

    Returns:
        An immutable equivalent of the input value.
    """
    if isinstance(value, dict):
        return MappingProxyType({k: freeze_recursive(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze_recursive(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze_recursive(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_recursive(item) for item in value)
    return value
