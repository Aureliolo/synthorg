"""Decode an argument a model sent as the JSON text of its value.

A model that cannot emit a nested array as a tool argument sends the text of
its JSON instead. A live planning session spent six of eleven turns
resubmitting one plan whose ``subtasks`` arrived as ``'[{...}]'``, refused
each time with "is not of type 'array'" against a schema its content had
satisfied on the first try; a reviewer in the same run sent ``'null'`` for a
nullable command field and was refused twice for naming a test command called
``null``. Each refusal was right about the type and useless about the fix,
because the fix is one ``json.loads`` the model cannot perform.

Decoding is bounded to what the schema DECLARES: a parameter whose declared
types do not include ``string`` and whose value is a string parsing to one of
the types they do include. A ``string`` parameter that happens to hold JSON is
left alone, since a file's content may well be JSON, and a string that does
not parse is left for the validator to refuse in its own words.
"""

import json
from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue

#: The Python shape each JSON-Schema type name admits once parsed.
_PARSED_TYPES: Final[Mapping[str, tuple[type, ...]]] = {
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
    "boolean": (bool,),
    "integer": (int,),
    "number": (int, float),
}
_TEXT: Final[str] = "string"


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


def _admits(parsed: object, type_names: frozenset[str]) -> bool:
    """Whether *parsed* is one of the shapes *type_names* declare.

    Returns:
        ``True`` when a declared type admits the parsed value. ``bool`` is
        checked before ``int`` because Python makes one a subclass of the
        other and JSON does not.
    """
    if isinstance(parsed, bool):
        return "boolean" in type_names
    return any(
        isinstance(parsed, _PARSED_TYPES[name])
        for name in type_names
        if name in _PARSED_TYPES
    )


def decode_json_encoded_arguments(
    schema: Mapping[str, JsonValue] | None,
    arguments: Mapping[str, JsonValue],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Replace JSON text with the value the schema declares for it.

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
        type_names = declared_types(properties.get(name))
        if _TEXT in type_names or not type_names & _PARSED_TYPES.keys():
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            continue
        if _admits(parsed, type_names):
            decoded[name] = parsed
            names.append(name)
    return decoded, tuple(names)
