# module-kind: code
"""Recognise a tool call the transport mangled, and say how to re-send it.

Some models serialise a repeated JSON array as XML siblings, and the provider
path collapses those siblings into nesting rather than into a list. What
arrives is a valid JSON object, so it clears every guard written against
malformed JSON (the driver's ``isinstance(parsed, dict)`` check included) and
is refused instead by the typed boundary, as a schema error naming a field the
model filled in correctly:

    {'$text': "...", 'item': {'$text': '...</item>', 'item': {...}}}

Nothing in this codebase emits ``$text`` or parses XML, so the key cannot
arrive from any legitimate caller: it is the transport's own artefact, and its
presence identifies the failure exactly. Telling the model that its list was
flattened, rather than that its arguments did not match the schema, is the
difference between a corrected resubmission and a blind one; two of thirteen
plan submissions in a live run arrived this way.
"""

from collections.abc import Mapping, Sequence
from typing import Final

#: The key the collapse leaves behind. An XML text node has no JSON spelling,
#: so a serialiser inventing one has to invent a name for it, and this is the
#: name every observed instance used.
_TEXT_NODE_KEY: Final[str] = "$text"

#: How deep to look. The nesting is one level per repeated element, so a long
#: list produces a long chain; the bound keeps a pathological argument tree
#: from turning a cheap check into a walk.
_MAX_DEPTH: Final[int] = 64

_HINT: Final[str] = (
    "Your tool call arrived with its repeated fields flattened into nested "
    f"{_TEXT_NODE_KEY!r} objects rather than a JSON array, so the arguments "
    "could not be read. This is a serialisation fault, not a mistake in your "
    "plan. Re-issue the SAME call, emitting every repeated field as a real "
    'JSON array of separate objects (for example "subtasks": [{...}, {...}]) '
    "with no wrapper element around the items."
)


def _carries_text_node(value: object, depth: int) -> bool:
    """Whether *value* holds the collapse artefact within *depth* levels.

    Returns:
        ``True`` when a mapping anywhere inside carries the text-node key.
    """
    if depth > _MAX_DEPTH:
        return False
    if isinstance(value, Mapping):
        if any(key == _TEXT_NODE_KEY for key in value):
            return True
        return any(_carries_text_node(item, depth + 1) for item in value.values())
    if isinstance(value, str | bytes):
        return False
    if isinstance(value, Sequence):
        return any(_carries_text_node(item, depth + 1) for item in value)
    return False


def mangled_serialisation_hint(arguments: object) -> str | None:
    """Say how to re-send *arguments*, or nothing when they arrived intact.

    Args:
        arguments: The tool call's arguments, as the provider path decoded
            them.

    Returns:
        The correction to hand the model, or ``None`` when nothing was
        mangled.
    """
    return _HINT if _carries_text_node(arguments, 0) else None


__all__ = ["mangled_serialisation_hint"]
