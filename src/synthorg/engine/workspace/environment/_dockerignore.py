# module-kind: code
"""``.dockerignore`` matching for the in-process build context.

``.dockerignore`` is a client-side convention: the ``docker`` CLI reads
it and omits the matching paths from the tar it uploads, and the daemon
knows nothing about it. Packing the context in-process therefore has to
implement it, or every declaration that relies on the file silently
ships the paths it excludes.

The pattern translation mirrors moby's ``patternmatcher``, which is what
an author's ``.dockerignore`` was written against: patterns are relative
to the context root, ``*`` and ``?`` stop at a path separator, ``**``
spans any number of segments, a ``!`` prefix re-includes, and the LAST
pattern that matches decides. A path is also excluded when any of its
parent directories is, which is what makes ``node_modules`` exclude the
tree beneath it rather than one empty directory entry.

One deliberate difference: a literal ``^`` in a pattern is escaped.
moby's own escape set omits it, so a filename containing one compiles to
a regex that means something else entirely; matching it literally is
what the author asked for either way.
"""

import re
from pathlib import Path
from typing import Final, NamedTuple

#: Regex metacharacters that must survive as literals. moby escapes the
#: first group; ``^`` is ours. ``[`` and ``]`` are deliberately absent:
#: a bracket expression is part of the pattern syntax and passes through.
_ESCAPED: Final[str] = ".+()|{}$^"

#: The Dockerfile-adjacent ignore file, which takes precedence over the
#: context-root one when a build names a Dockerfile explicitly.
_DOCKERIGNORE: Final[str] = ".dockerignore"


class _Rule(NamedTuple):
    """One compiled ``.dockerignore`` line.

    Attributes:
        pattern: The line compiled to a full-match regex over a
            context-relative POSIX path.
        negated: Whether the line began with ``!``, re-including what an
            earlier line excluded.
    """

    pattern: re.Pattern[str]
    negated: bool


def _translate(pattern: str) -> str:
    r"""Compile one cleaned pattern to a full-match regex source.

    Args:
        pattern: A separator-normalised pattern with no ``!`` prefix.

    Returns:
        The regex source, anchored at both ends. The tail anchor is
        ``\\Z`` rather than ``$`` because ``$`` also matches before a
        trailing newline, and a newline is a legal character in a
        filename.
    """
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        index += 1
        if char != "*":
            if char == "?":
                out.append("[^/]")
            elif char in _ESCAPED:
                out.append("\\" + char)
            else:
                out.append(char)
            continue
        if index < length and pattern[index] == "*":
            index += 1
            # ``**/`` and ``**`` mean the same thing, so the separator is
            # eaten rather than being required to match one.
            if index < length and pattern[index] == "/":
                index += 1
            # A trailing ``**`` accepts everything below, matching
            # .gitignore; elsewhere it spans zero or more segments.
            out.append(".*" if index >= length else "(.*/)?")
        else:
            out.append("[^/]*")
    return "^" + "".join(out) + r"\Z"


def _clean(line: str) -> str:
    """Normalise one raw line to a comparable pattern.

    Returns:
        The pattern with separators normalised and the leading and
        trailing slashes that carry no meaning removed. Empty when the
        line contributes nothing.
    """
    return line.strip().replace("\\", "/").strip("/")


def _compile(line: str) -> _Rule | None:
    """Compile one raw ``.dockerignore`` line.

    Returns:
        The rule, or ``None`` for a blank line or a comment.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    negated = stripped.startswith("!")
    cleaned = _clean(stripped[1:] if negated else stripped)
    if not cleaned:
        return None
    return _Rule(pattern=re.compile(_translate(cleaned)), negated=negated)


def _self_and_parents(relative_path: str) -> tuple[str, ...]:
    """Return *relative_path* and every directory above it.

    Returns:
        The path itself first, then each ancestor, so a pattern naming a
        directory excludes everything beneath it.
    """
    parts = relative_path.split("/")
    return tuple("/".join(parts[: index + 1]) for index in reversed(range(len(parts))))


class DockerignoreMatcher:
    """Decides whether a context-relative path is excluded."""

    def __init__(self, rules: tuple[_Rule, ...]) -> None:
        self._rules = rules

    def __bool__(self) -> bool:
        """Whether any rule was parsed.

        Returns:
            ``True`` when the matcher can exclude something.
        """
        return bool(self._rules)

    def excludes(self, relative_path: str) -> bool:
        """Whether *relative_path* is excluded from the build context.

        Args:
            relative_path: A context-relative POSIX path, with no
                leading ``./``.

        Returns:
            ``True`` when the last matching rule excludes it.
        """
        candidates = _self_and_parents(relative_path)
        excluded = False
        for rule in self._rules:
            if any(rule.pattern.match(candidate) for candidate in candidates):
                excluded = not rule.negated
        return excluded


def parse_dockerignore(text: str) -> DockerignoreMatcher:
    """Build a matcher from the contents of a ``.dockerignore`` file.

    Args:
        text: The file's contents.

    Returns:
        A matcher over the file's rules, in file order.
    """
    rules = tuple(rule for line in text.splitlines() if (rule := _compile(line)))
    return DockerignoreMatcher(rules)


def load_dockerignore(context_dir: Path, dockerfile: Path) -> DockerignoreMatcher:
    """Read the ignore file governing a build, if there is one.

    Docker looks for ``<dockerfile>.dockerignore`` first and falls back
    to the one at the context root, so a repository holding several
    Dockerfiles can give each its own exclusions.

    Args:
        context_dir: The resolved build-context root.
        dockerfile: The resolved Dockerfile path.

    Returns:
        A matcher over the first candidate that reads, empty when
        neither does. An unreadable file is passed over rather than
        raised on: it is one the author cannot have been relying on,
        and every path it would have excluded is separately covered by
        the unconditional exclusions the packer applies.
    """
    candidates = (
        dockerfile.with_name(f"{dockerfile.name}{_DOCKERIGNORE}"),
        context_dir / _DOCKERIGNORE,
    )
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        return parse_dockerignore(text)
    return DockerignoreMatcher(())


__all__ = ["DockerignoreMatcher", "load_dockerignore", "parse_dockerignore"]
