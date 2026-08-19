# module-kind: code
"""Name a conversation by the sentence that opened it.

A row's ``ConversationKind`` names a category, not a conversation: a session
that files twenty work requests has twenty rows in the same category, and a
drawer whose whole purpose is picking one out has to say what makes each one
different. Only its own opening sentence does.

The title is DERIVED, never stored. A stored title is a second copy of
something the transcript already holds, and the two disagree the moment the
opening turn is edited, purged or re-attributed. Deriving it also means every
conversation ever recorded is named, including the ones that predate this
being asked for.

The opening turn is the operator's own words, so the derivation only ever
trims: no summarisation, no LLM, no rewriting. What a person typed is what
labels their conversation.
"""

import re
from typing import Final

#: Longest rendered title. Sized for a drawer row rather than a document
#: heading; the full sentence is one click away in the transcript itself.
_MAX_TITLE_CHARS: Final[int] = 80

#: Appended when the sentence was cut, so a trimmed title is visibly trimmed
#: rather than reading as a short message. Its own length comes off the budget,
#: so the bound holds for what is rendered rather than for what was kept.
_ELLIPSIS: Final[str] = "…"

#: Leading Markdown structure an agent-facing composer may have added. Stripped
#: because it is formatting rather than words: a row reading "## Build me a
#: dashboard" shows the operator punctuation they did not intend as a title.
#:
#: Ordered-list markers count, and need their own alternative because a digit
#: is otherwise a word: the delimiter and the space after it are what make
#: "1. Build a dashboard" a list rather than a sentence starting with a number,
#: so "1.5 million users" and "3 things I want" keep every character.
_LEADING_MARKUP: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:[#>*+\-]+[ \t]*|\d+[.)][ \t]+)+"
)

_WHITESPACE: Final[re.Pattern[str]] = re.compile(r"\s+")


def derive_conversation_title(content: str) -> str | None:
    """Derive a one-line title from a conversation's opening message.

    Args:
        content: The opening turn's raw content, exactly as it was stored.

    Returns:
        A single line of at most :data:`_MAX_TITLE_CHARS` characters, or
        ``None`` when the message carries no words to name it by. ``None`` is
        the honest answer rather than a placeholder: the caller already has a
        fallback that says what kind of conversation this is, and inventing a
        title here would take that choice away from it.
    """
    collapsed = _WHITESPACE.sub(" ", _LEADING_MARKUP.sub("", content)).strip()
    if not collapsed:
        return None
    if len(collapsed) <= _MAX_TITLE_CHARS:
        return collapsed
    # Cut at the last word boundary inside the budget so a title never ends
    # mid-word. A single word longer than the whole budget has no boundary to
    # cut at, and is trimmed hard rather than rendered whole.
    head = collapsed[: _MAX_TITLE_CHARS - len(_ELLIPSIS)]
    boundary = head.rfind(" ")
    trimmed = head[:boundary] if boundary > 0 else head
    return f"{trimmed.rstrip()}{_ELLIPSIS}"


__all__ = ["derive_conversation_title"]
