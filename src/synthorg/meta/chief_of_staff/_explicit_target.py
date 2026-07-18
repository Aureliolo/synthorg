# module-kind: code
"""Explicit ``@role`` / ``@name`` addressing for the unified turn surface.

An operator can prefix a message with one or more ``@Target`` tokens to
address a specific role or agent directly, overriding the model-inferred
route. This leaf module parses those leading mentions off the message so
the turn orchestrator can resolve them against the roster and construct an
explicit responder (or the participant set for a convened group), while the
remaining text is what the responder actually answers.
"""

import re

from synthorg.core.types import NotBlankStr

# A leading mention: ``@`` then a role/name token (letters, digits, and
# internal ``_``/``-``/``.``), optionally followed by whitespace. Anchored to
# the current start so only a run of mentions at the very front of the
# message is consumed; an ``@`` mid-sentence is left in the body untouched.
_LEADING_MENTION = re.compile(r"^\s*@([A-Za-z][A-Za-z0-9_.-]*)\s*")


def extract_explicit_targets(message: str) -> tuple[str, tuple[NotBlankStr, ...]]:
    """Split leading ``@Target`` mentions off *message*.

    Consumes only mentions at the very start of the message, in order, so a
    later ``@`` inside prose stays part of the body. When the message is
    nothing but mentions (no remaining text), the original message is kept
    as the body so a turn is never left empty.

    Args:
        message: The raw operator message, possibly prefixed with mentions.

    Returns:
        A ``(body, targets)`` pair: the message with leading mentions
        stripped, and the addressed targets in the order given (empty when
        none were addressed).
    """
    targets: list[NotBlankStr] = []
    rest = message
    while (match := _LEADING_MENTION.match(rest)) is not None:
        targets.append(NotBlankStr(match.group(1)))
        rest = rest[match.end() :]
    body = rest.strip() or message.strip()
    return body, tuple(targets)


__all__ = ["extract_explicit_targets"]
