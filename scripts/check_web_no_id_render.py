#!/usr/bin/env python3
"""Gate: the dashboard never prints an identifier where a name belongs.

An id is a database key. It is not memorable, not comparable by eye, and it
crowds out the name it stands in for; where it lands in prose it tells the
operator that a UUID is talking to them. So the backend resolves every actor
reference to a name at the read boundary and sends both, and the surface
renders the name or its own words for "nobody" -- never the key.

What this checks, precisely: a JSX **text child** whose expression ends in an
id-shaped name. That is the position where a value is read as prose, and it is
the one a regex can decide without a TypeScript parser. A container is a text
child when the last non-whitespace character before it is not one that makes it
something else, so prose on either side (``Owner: {plan.owner}``) is read like
a container that sits flush against its tag.

What it deliberately does NOT check, so nobody mistakes silence for coverage:

* Attributes. ``key={t.id}``, ``to={...t.id}`` and ``value={a.id}`` are how an
  id is legitimately used, and separating those from a rendering attribute
  needs to know the component.
* A value already destructured out of its object (``const { owner } = plan``
  and then ``{owner}``). The rule is decided on the member-access path, and
  after destructuring there is no path left to read.
* A ternary (``{t.owner ? t.owner : 'Unassigned'}``). The leading path there is
  a CONDITION, not the printed value; the printed values are in the branches,
  and reading them needs to know which branch is which.
* Whether the name it renders instead is the right one. That is a test.
* Anything outside ``web/src/``.

Opt out with a ``lint-allow: no-id-render -- <reason>`` marker on the rendering
line or the one directly above it (a ``//`` comment in TS, a ``{/* */}``
comment in JSX, which cannot sit inside the text it annotates without becoming
a child node of it). The justification after ``--`` is required. There is no
baseline file: the rule ships with zero offenders.

Run from the repository root. Exits non-zero on any violation.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Final

WEB_SRC: Final[str] = "web/src"

SUPPRESSION_MARKER: Final[str] = "lint-allow: no-id-render"

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*no-id-render\s*--\s*\S",
)

#: Path fragments marking a test / story file. Both render the same JSX, but
#: their fixtures are authored values rather than wire data, and a story that
#: deliberately shows the unresolved state is a legitimate thing to build.
_EXEMPT_MARKERS: Final[tuple[str, ...]] = (
    "__tests__",
    ".test.",
    ".stories.",
    "test-infra",
)

#: Declared rather than derived from an ``_id`` suffix, because the suffix
#: does not decide the question. A model identifier IS what an operator picks
#: a model by; a backup, a simulation run and a workflow node have no name at
#: all, and printing "unknown" in place of their reference would lose the only
#: handle there is. What belongs here is a key into something that HAS a human
#: name, which is exactly the set the backend resolves one for. It grows with
#: that set.
_KEYED_REFERENCES: Final[frozenset[str]] = frozenset(
    {
        # People.
        "agent_id",
        "assigned_to",
        "author_agent_id",
        "created_by",
        "decided_by",
        "executor",
        "executor_agent_id",
        "lead",
        "owner",
        "requested_by",
        "reviewer",
        "reviewer_agent_id",
        # Entities with a title.
        "item_id",
        "parent_task_id",
        "plan_id",
        "plan_item_id",
        "project_id",
        "task_id",
    }
)

#: An expression container holding no nested braces, so a nested object literal
#: or an arrow-function child is not matched: those are not a bare value being
#: printed, which is the only shape this decides. Which containers are text
#: children is decided by :func:`_is_text_child` rather than by anchoring the
#: pattern, because anchoring to a delimiter misses every container with prose
#: beside it, and consuming one hides the second of two adjacent containers.
_EXPRESSION_CONTAINER_RE: Final[re.Pattern[str]] = re.compile(r"\{([^{}]+)\}")

#: What the last non-whitespace character before a container means it is, when
#: it is one of these: an attribute value (``key={t.id}``), a template
#: substitution (``${t.id}``), or the inner half of a nested literal
#: (``style={{...}}``). None of the three is prose.
_NOT_A_TEXT_CHILD: Final[frozenset[str]] = frozenset({"=", "$", "{"})

#: The name at the end of the rendered expression, ignoring an optional
#: ``?? fallback`` or ``|| fallback`` tail. ``a.b.c`` yields ``c``.
_LEADING_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*[A-Za-z_$][\w$]*(?:(?:\?)?\.[A-Za-z_$][\w$]*)+",
)


def _is_id_shaped(name: str) -> bool:
    """Whether *name* is a key into something that has a human name.

    Returns:
        ``True`` when *name* is one of the declared keyed references.
    """
    return name in _KEYED_REFERENCES


def _rendered_name(expression: str) -> str | None:
    """Return the final segment of the value *expression* renders.

    Only a plain member-access path counts. A call, an index, a template
    literal or a comparison is not a bare value being printed, and reading a
    name out of one would be guessing.

    Returns:
        The last path segment, or ``None`` when the expression is not one.
    """
    match = _LEADING_PATH_RE.match(expression)
    if match is None:
        return None
    tail = expression[match.end() :].strip()
    # A fallback is the one tail that leaves the head a printed value.
    if tail and not tail.startswith(("??", "||")):
        return None
    return match.group(0).replace("?.", ".").rsplit(".", maxsplit=1)[1]


def _is_text_child(source: str, start: int) -> bool:
    """Whether the container at *start* is prose rather than something else.

    Read backwards past whitespace so a container on its own line is judged by
    the character that put it there, not by the newline.

    Returns:
        ``True`` when the container is a JSX text child.
    """
    before = source[:start].rstrip()
    return bool(before) and before[-1] not in _NOT_A_TEXT_CHILD


def _line_of(text: str, index: int) -> int:
    """Return the 1-based line number of *index* in *text*.

    Returns:
        The line number.
    """
    return text.count("\n", 0, index) + 1


def _violations(path: Path, source: str) -> list[str]:
    """Report every id-shaped value rendered as JSX text in *source*.

    Returns:
        One message per violation.
    """
    lines = source.splitlines()
    found: list[str] = []
    for match in _EXPRESSION_CONTAINER_RE.finditer(source):
        if not _is_text_child(source, match.start()):
            continue
        name = _rendered_name(match.group(1))
        if name is None or not _is_id_shaped(name):
            continue
        lineno = _line_of(source, match.start(1))
        # The line, or the one above it: a JSX comment cannot sit inside the
        # text it annotates without becoming a child node of it, so the marker
        # for a text child goes on its own line directly above.
        window = lines[max(lineno - 2, 0) : lineno]
        if any(_SUPPRESSION_RE.search(line) for line in window):
            continue
        found.append(
            f"{path.as_posix()}:{lineno}: renders {name!r}, which is an "
            "identifier. The backend sends a resolved name beside it; render "
            "that, and your own words when it is absent."
        )
    return found


def _scan(root: Path) -> list[str]:
    """Scan every tracked web source under *root*.

    Returns:
        One message per violation, in path order.
    """
    web_src = root / WEB_SRC
    if not web_src.is_dir():
        return [f"{WEB_SRC} is missing; the gate cannot verify anything."]
    messages: list[str] = []
    for path in sorted(web_src.rglob("*.tsx")):
        rel = path.relative_to(root).as_posix()
        if any(marker in rel for marker in _EXEMPT_MARKERS):
            continue
        messages.extend(_violations(path.relative_to(root), path.read_text("utf-8")))
    return messages


def main() -> int:
    """Run the gate.

    Returns:
        ``0`` when clean, ``1`` on any violation.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path())
    args = parser.parse_args()
    messages = _scan(args.repo_root)
    if not messages:
        return 0
    for message in messages:
        print(message, file=sys.stderr)
    print(
        f"\n{len(messages)} identifier(s) rendered where a name belongs.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
