"""Decode a structured argument a model sent as JSON text.

A model that cannot emit a nested array as a tool argument sends the text of
its JSON instead. A live planning session spent six of eleven turns
resubmitting one plan whose ``subtasks`` arrived as ``'[{...}]'``, refused
each time with "is not of type 'array'" against a schema its content had
satisfied on the first try. The refusal was right about the type and useless
about the fix, because the fix is one ``json.loads`` the model cannot perform.

Decoding is bounded to what the schema DECLARES structured: a parameter typed
``array`` or ``object`` whose value is a string parsing to exactly that shape.
A ``string`` parameter that happens to hold JSON is left alone, since a file's
content may well be JSON, and a string that does not parse is left for the
validator to refuse in its own words.
"""

import json
from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue

_STRUCTURED_TYPES: Final[Mapping[str, type[list[object]] | type[dict[str, object]]]] = {
    "array": list,
    "object": dict,
}


def declared_types(property_schema: object) -> frozenset[str]:
    """The JSON-Schema type names a property declares, through unions.

    Returns:
        Every ``type`` named directly or inside ``anyOf`` / ``oneOf``.
    """
    if not isinstance(property_schema, dict):
        return frozenset()
    names: set[str] = set()
    declared = property_schema.get("type")
    if isinstance(declared, str):
        names.add(declared)
    elif isinstance(declared, list):
        names.update(item for item in declared if isinstance(item, str))
    for key in ("anyOf", "oneOf"):
        options = property_schema.get(key)
        if isinstance(options, list):
            for option in options:
                names |= declared_types(option)
    return frozenset(names)


def decode_json_encoded_arguments(
    schema: Mapping[str, JsonValue] | None,
    arguments: Mapping[str, JsonValue],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Replace JSON text with the structure the schema declares for it.

    Returns:
        The arguments with every decodable value decoded, and the names of
        the parameters that were.
    """
    decoded: dict[str, object] = dict(arguments)
    properties = schema.get("properties") if schema is not None else None
    if not isinstance(properties, dict):
        return decoded, ()
    names: list[str] = []
    for name, value in arguments.items():
        if not isinstance(value, str):
            continue
        wanted = tuple(
            _STRUCTURED_TYPES[type_name]
            for type_name in declared_types(properties.get(name))
            if type_name in _STRUCTURED_TYPES
        )
        if not wanted:
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            continue
        if isinstance(parsed, wanted):
            decoded[name] = parsed
            names.append(name)
    return decoded, tuple(names)
