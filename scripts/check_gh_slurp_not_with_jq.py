#!/usr/bin/env python3
r"""Pre-commit gate: ``gh api`` pagination flags that cannot mean what they read as.

Two shapes, neither of which any shell linter can see, because both are
``gh`` CLI semantics rather than shell syntax.

RULE 1, ``--slurp`` beside a filter flag. ``gh`` refuses it outright::

    the `--slurp` option is not supported with `--jq` or `--template`

and exits 1 BEFORE making the request, so the invocation never runs. The
two flags are reached for together for a good reason: ``--paginate --jq``
applies the filter once per page, which makes ``first`` mean
first-of-page, and ``--slurp`` is the documented fix for exactly that.
Writing both is the natural next step and is the one thing that cannot
work.

RULE 2, ``--paginate`` with a filter that COLLAPSES an array and no
``--slurp`` to fold the pages first. This one runs, which makes it the
worse of the two: the filter is applied per page, so ``first`` / ``.[0]``
/ ``last`` / ``length`` answer once per page and the caller reads one
line per matching page where it expected one value. A filter that STREAMS
(``.[] | select(...) | .id``) is unaffected and is not flagged, because
per-page streaming concatenates to the same result.

The correct form for both pipes the slurped array into its own ``jq``::

    gh api "<endpoint>" --paginate --slurp \
      | jq -r "flatten(1) | map(select(...)) | first | .id // empty"

``flatten(1)`` is load-bearing there and is why the example carries it:
``--paginate --slurp`` yields one array PER PAGE, so a selection written
against a flat array silently matches nothing. It is a no-op on an
already-flat array, so it is correct either way.

Scope, stated because a gate that reads broader than it is becomes the
reason nobody looks again:

* Statements are reconstructed from backslash continuations (ODD parity
  only, since a trailing ``\\`` is an escaped backslash and not a
  continuation) and from YAML folded block scalars (``run: >``), which are
  joined by YAML rather than by the shell and would otherwise be scanned
  as unrelated lines.
* Shell comments are removed first. Prose about these very flags appears
  in several workflow comments, including the ones the fix left behind.
* Flags count only within ONE gh invocation, ending at the first unquoted
  ``|``, ``;`` or ``&``. Without that, ``... --slurp | jq -r . | grep -q x``
  reports the ``-q`` of a downstream ``grep`` as gh's own filter flag, and
  the gate rejects the very form it recommends.
* ``gh`` reached through a name this file cannot resolve (a variable, an
  alias) is out of reach of any static scan.

Rule 1 has no opt-out: an invocation that cannot execute is never worth
preserving. Rule 2 has one (``# lint-allow: paginate-aggregate -- <reason>``
anywhere in the statement's line span), because a per-page aggregate the
caller genuinely recombines downstream is a real shape, and the reason is
the only place that claim gets written down.

Usage::

    python scripts/check_gh_slurp_not_with_jq.py --scan-all   # pre-commit + CI
    python scripts/check_gh_slurp_not_with_jq.py <file>...     # ad hoc
"""

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GITHUB_ROOT = _REPO_ROOT / ".github"

_YAML_SUFFIXES = (".yml", ".yaml")

# `gh_retry gh` and `gh_api_retry` are this repo's own wrappers; both
# forward their arguments to `gh api` unchanged, so a flag pair written
# through one reaches gh exactly as it would written directly. The inner
# alternative tolerates global flags (`gh -R owner/repo api`), which gh
# accepts before the subcommand, without letting the flag's value eat the
# `api` token itself.
_GH_API_CALL = re.compile(
    r"\b(?:gh_api_retry"
    r"|gh(?:_retry\s+gh)?"
    r"(?:\s+-{1,2}[\w-]+(?:=\S+)?(?:\s+(?!api\b)[^-\s]\S*)?)*"
    r"\s+api)\b"
)
_SLURP = re.compile(r"(?<![\w-])--slurp(?![\w-])")
# `=` is deliberately absent from the trailing lookahead: `--jq=<expr>` is
# a spelling gh accepts, so excluding it would leave the equals form free.
_FILTER = re.compile(r"(?<![\w-])(?:--jq|-q|--template|-t)(?![\w-])")
_PAGINATE = re.compile(r"(?<![\w-])--paginate(?![\w-])")

# Selectors that reduce an array to a single value. Applied per page, each
# answers once per page instead of once. A streaming filter has none of
# them and is correct under `--paginate` without `--slurp`.
_AGGREGATE = re.compile(r"(?<![\w.])(?:first|last|length|add)\b|\.\[0\]")

_STATEMENT_BREAK = "|;&"
_FOLDED_BLOCK = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:\s*>[-+]?\s*$")

_OPT_OUT = re.compile(r"#\s*lint-allow:\s*paginate-aggregate\s*--\s*\S+")

_RULE_SLURP_WITH_FILTER = "slurp-with-filter"
_RULE_PAGINATE_AGGREGATE = "paginate-aggregate"

_STEERING_MESSAGE = (
    "`gh api --slurp` cannot be combined with `--jq` / `-q` / `--template` "
    "/ `-t`; gh exits 1 before making the request. And `--paginate` "
    "without `--slurp` applies the filter once per page, so a collapsing "
    "selector (`first`, `.[0]`, `last`, `length`, `add`) answers once per "
    "page. Pipe the slurped array into its own jq instead:\n"
    '    gh api "<endpoint>" --paginate --slurp \\\n'
    '      | jq -r "flatten(1) | map(select(...)) | first | .id // empty"\n'
    "`flatten(1)` is required: --paginate --slurp yields one array per page."
)


class _UnreadableFileError(RuntimeError):
    """Raised when a scanned file cannot be read or decoded as UTF-8.

    Covers a decode failure and any OS-level read error alike. Callers
    promote it to a violation so the gate never fails open on a file it
    could not inspect.
    """


@dataclasses.dataclass(frozen=True)
class _Hit:
    """One offending gh invocation."""

    lineno: int
    rule: str
    statement: str


@dataclasses.dataclass(frozen=True)
class _Statement:
    """One logical shell statement reassembled from several source lines."""

    text: str
    first_line: int
    last_line: int
    # Offset of each contributing line within ``text``, ascending, so an
    # offset can be attributed to the line a reader would actually edit.
    line_offsets: tuple[tuple[int, int], ...]

    def line_of(self, offset: int) -> int:
        """Return the source line number containing ``offset``."""
        lineno = self.first_line
        for start, candidate in self.line_offsets:
            if start > offset:
                break
            lineno = candidate
        return lineno


def _unquoted(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(index, char)`` for each character outside quotes.

    Single and double quoted runs are skipped whole, so a `|` inside a jq
    program is not mistaken for a shell pipe and a `#` inside a string is
    not mistaken for a comment.
    """
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        yield index, char
        index += 1


def _strip_comment(line: str) -> str:
    """Drop a trailing shell/YAML comment, respecting quotes.

    A `#` only opens a comment at the start of a word, so `foo#bar` and a
    `#` inside a quoted jq program both survive.
    """
    for index, char in _unquoted(line):
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _continues(line: str) -> bool:
    """Whether ``line`` ends in a shell line continuation.

    Only an ODD run of trailing backslashes continues; an even run is
    escaped backslashes and the statement ends there.
    """
    stripped = line.rstrip()
    trailing = len(stripped) - len(stripped.rstrip("\\"))
    return trailing % 2 == 1


def _folded_block_lines(lines: list[str], start: int) -> int:
    """Return the index one past the folded block opened at ``start``.

    A folded scalar's body is the run of following lines indented more
    than its ``run:`` key. A blank line ends the fold for our purposes,
    since YAML turns it into a hard newline.
    """
    match = _FOLDED_BLOCK.match(lines[start])
    if match is None:
        return start + 1
    key_indent = len(match.group("indent"))
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            break
        indent = len(line) - len(line.lstrip())
        if indent <= key_indent:
            break
        index += 1
    return index


def _join(pieces: list[tuple[str, int]]) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Join line pieces with single spaces, recording each one's offset."""
    text = ""
    offsets: list[tuple[int, int]] = []
    for piece, lineno in pieces:
        if text:
            text += " "
        offsets.append((len(text), lineno))
        text += piece
    return text, tuple(offsets)


def _statements(source: str) -> Iterator[_Statement]:
    """Yield every logical statement in ``source``.

    Backslash continuations and YAML folded block scalars both produce one
    statement from several lines; everything else is one line each.
    """
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        if _FOLDED_BLOCK.match(lines[index]):
            end = _folded_block_lines(lines, index)
            pieces = [
                (_strip_comment(lines[i]).strip(), i + 1) for i in range(index + 1, end)
            ]
            pieces = [(text, lineno) for text, lineno in pieces if text]
            if pieces:
                text, offsets = _join(pieces)
                yield _Statement(text, pieces[0][1], pieces[-1][1], offsets)
            index = max(end, index + 1)
            continue

        start = index
        pieces = [(_strip_comment(lines[index]), index + 1)]
        while _continues(pieces[-1][0]) and index + 1 < len(lines):
            index += 1
            head, lineno = pieces[-1]
            pieces[-1] = (head.rstrip()[:-1], lineno)
            pieces.append((_strip_comment(lines[index]).strip(), index + 1))
        # A continuation on the final line has nothing to join to; drop the
        # dangling backslash so it does not reach the reported statement.
        head, lineno = pieces[-1]
        if _continues(head):
            pieces[-1] = (head.rstrip()[:-1], lineno)
        text, offsets = _join(pieces)
        if text.strip():
            yield _Statement(text, start + 1, index + 1, offsets)
        index += 1


def _shell_commands(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(offset, text)`` for each shell command in a statement.

    Commands are separated by unquoted ``|``, ``;`` and ``&``. A second
    ``gh api`` inside ONE command is not a second invocation: a trailing
    backslash makes the shell pass it to the first gh as arguments, so gh
    sees every flag on the joined line and the whole thing is one call.
    """
    start = 0
    for index, char in _unquoted(text):
        if char in _STATEMENT_BREAK:
            yield start, text[start:index]
            start = index + 1
    yield start, text[start:]


def _filter_arguments(segment: str) -> list[str]:
    """Return the program text of every filter flag in one invocation."""
    arguments: list[str] = []
    for match in _FILTER.finditer(segment):
        rest = segment[match.end() :]
        if rest.startswith("="):
            rest = rest[1:]
        else:
            rest = rest.lstrip()
        if not rest:
            continue
        if rest[0] in "'\"":
            quote = rest[0]
            closing = rest.find(quote, 1)
            arguments.append(rest[1:closing] if closing > 0 else rest[1:])
        else:
            arguments.append(rest.split(maxsplit=1)[0])
    return arguments


def _classify(segment: str) -> str | None:
    """Return the rule ``segment`` violates, or ``None`` when it is clean."""
    has_filter = _FILTER.search(segment) is not None
    if _SLURP.search(segment) and has_filter:
        return _RULE_SLURP_WITH_FILTER
    if _SLURP.search(segment) or not _PAGINATE.search(segment) or not has_filter:
        return None
    if any(_AGGREGATE.search(argument) for argument in _filter_arguments(segment)):
        return _RULE_PAGINATE_AGGREGATE
    return None


def _opted_out(lines: list[str], statement: _Statement) -> bool:
    """Whether the statement's own line span carries the rule-2 marker."""
    span = lines[statement.first_line - 1 : statement.last_line]
    return any(_OPT_OUT.search(line) for line in span)


def _scan_file(path: Path) -> list[_Hit]:
    """Return one ``_Hit`` per offending ``gh api`` invocation in ``path``.

    Each hit's line number is the line its own invocation STARTS on, so
    two independent calls in one continued statement report separately
    rather than both pointing at the first.

    Raises:
        _UnreadableFileError: when the file cannot be read or decoded.
            Callers promote that to a violation, so the gate never passes
            a file it could not inspect.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        msg = f"{_rel(path)}: could not read file: {type(exc).__name__}: {exc}"
        raise _UnreadableFileError(msg) from exc

    raw_lines = source.splitlines()
    hits: list[_Hit] = []
    for statement in _statements(source):
        for offset, command in _shell_commands(statement.text):
            call = _GH_API_CALL.search(command)
            if call is None:
                continue
            rule = _classify(command)
            if rule is None:
                continue
            if rule == _RULE_PAGINATE_AGGREGATE and _opted_out(raw_lines, statement):
                continue
            hits.append(
                _Hit(
                    lineno=statement.line_of(offset + call.start()),
                    rule=rule,
                    statement=" ".join(command.split()),
                )
            )
    return hits


def _iter_workflow_files() -> list[Path]:
    """Every YAML file under ``.github/`` (workflows, composites, configs)."""
    if not _GITHUB_ROOT.exists():
        return []
    found: list[Path] = []
    for pattern in ("*.yml", "*.yaml"):
        found.extend(_GITHUB_ROOT.rglob(pattern))
    return sorted(found)


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable error output."""
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _report(violations: list[str]) -> int:
    """Print violations plus the steering message; return the exit code."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(f"\n{_STEERING_MESSAGE}", file=sys.stderr)
    return 1


def _collect(paths: Iterable[Path]) -> list[str]:
    """Scan each path, promoting read failures to violations."""
    violations: list[str] = []
    for path in paths:
        try:
            hits = _scan_file(path)
        except _UnreadableFileError as exc:
            violations.append(str(exc))
            continue
        violations.extend(
            f"{_rel(path)}:{hit.lineno}: [{hit.rule}] {hit.statement}" for hit in hits
        )
    return violations


def cmd_scan_all() -> int:
    """Walk every YAML file under ``.github/`` and report every hit.

    An unreachable or empty ``.github/`` is a configuration error rather
    than a clean repo: it means this run inspected nothing, and reporting
    that as success is indistinguishable from a genuine zero-violation
    scan. Exit 2 keeps that apart from exit 1, which means violations.
    """
    if not _GITHUB_ROOT.exists():
        print(
            f"::error::{_rel(_GITHUB_ROOT)} does not exist; nothing was scanned",
            file=sys.stderr,
        )
        return 2
    files = _iter_workflow_files()
    if not files:
        print(
            f"::error::no YAML files under {_rel(_GITHUB_ROOT)}; nothing was scanned",
            file=sys.stderr,
        )
        return 2
    return _report(_collect(files))


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the provided files only.

    Every supplied path failing the suffix or containment filter is a
    caller mismatch rather than an empty commit, so it exits 2 instead of
    reporting a scan that never happened as clean.
    """
    supplied = list(paths)
    selected: list[Path] = []
    dropped: list[str] = []
    for candidate in supplied:
        path = Path(candidate).resolve()
        if not path.exists() or path.suffix not in _YAML_SUFFIXES:
            dropped.append(candidate)
            continue
        if not path.is_relative_to(_GITHUB_ROOT):
            dropped.append(candidate)
            continue
        selected.append(path)
    if supplied and not selected:
        print(
            f"::error::all {len(supplied)} supplied path(s) were filtered out "
            f"(wrong suffix, or not under {_rel(_GITHUB_ROOT)}); "
            f"nothing was scanned: {dropped}",
            file=sys.stderr,
        )
        return 2
    return _report(_collect(selected))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Block `gh api` pagination flags that cannot mean what "
        "they read as.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check.",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan every YAML file under .github/ (pre-commit and CI mode).",
    )
    args = parser.parse_args(argv)
    if args.scan_all:
        return cmd_scan_all()
    return cmd_scan_paths(args.paths)


if __name__ == "__main__":
    sys.exit(main())
