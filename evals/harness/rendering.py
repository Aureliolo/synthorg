# module-kind: code
"""Making agent-authored text safe to put in front of a person.

Everything a recording captures is written by a model or by the tree a model
delivered, which this harness treats as adversarial by construction: the
grading container has no network and no credentials for exactly that reason.
The reports are the one place that content comes back OUT, into a terminal
that acts on what it is sent.

The surfaces are narrower than they look and none of them is a free-text blob.
A tool-call NAME comes off the response deltas, so it is whatever the model
emitted rather than anything the harness offered. A shell VERB is the first
word of a command the model wrote. A module PATH is a filename in a delivered
tree, and a filename is the least constrained of the three: a unit may name a
file anything the filesystem accepts, escape sequences included.

One owner, because the alternative was measured: the same filter existed in
one module and three newer reports printed model-authored strings straight to
stdout without it, each of them a report written after the rule was already
established somewhere else.
"""

from typing import Final

#: How much of a failing run's output travels with a record. Bounded because
#: this ends up in a committed JSON artifact as well as in a terminal.
TAIL_CHARS: Final[int] = 800


def printable(text: str) -> str:
    """Strip *text* of everything a terminal would act on rather than show.

    Control characters are dropped rather than escaped: the destinations are a
    terminal and a committed JSON artifact, and an escape sequence from a
    delivered tree has no business reaching either. Newlines survive because
    the callers that keep them are rendering multi-line output.

    Args:
        text: Agent-authored text on its way to a person.

    Returns:
        The same text, printable characters and newlines only.
    """
    return "".join(char for char in text if char.isprintable() or char == "\n")


def one_line(text: str) -> str:
    """Strip *text* and flatten it onto a single line.

    For a value rendered inside a table row or beside other fields, where a
    newline would break the alignment the row depends on.

    Args:
        text: Agent-authored text on its way to a person.

    Returns:
        The text with control characters and newlines removed.
    """
    return "".join(char for char in text if char.isprintable())


def tail(output: str) -> str:
    """Bound and flatten agent-authored output for a record.

    Args:
        output: The captured output.

    Returns:
        The trailing extract, printable characters only.
    """
    return printable(output)[-TAIL_CHARS:].strip()


__all__ = ["TAIL_CHARS", "one_line", "printable", "tail"]
