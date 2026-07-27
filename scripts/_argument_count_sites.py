#!/usr/bin/env python3
"""Discovery half of the argument-count gate: who is over the cap, and why.

The gate that consumes this module must answer "is every over-cap function
accounted for", and that question is only as good as the population it starts
from. Asking ``ruff`` for its diagnostics and treating the answer as the whole
population is wrong in two directions at once:

* ``ruff`` exempts some over-cap functions by design. A method decorated with
  ``@typing.override`` is exempt from ``PLR0913`` **syntactically**, with no
  base class required and no type inference involved. Three such methods exist
  in this tree, one of them taking thirteen arguments.
* ``ruff`` never visits some files at all. ``[tool.ruff] exclude`` /
  ``extend-exclude`` and (by default) every ``.gitignore`` pattern prune its
  directory walk, and a pruned file produces no diagnostics whether or not it
  contains a violation.

So this module derives the population itself, from the AST, and the gate diffs
that against what ``ruff`` reported. A candidate ``ruff`` never mentioned is not
"clean": it is :attr:`SiteStatus.RULE_EXEMPT`, and it needs an approved baseline
entry exactly like a suppressed one. That inverts the trust relationship:
``ruff`` classifies, this module decides who is in scope.

Parsing every tracked file to find that population costs twenty seconds, which
is most of a gate that otherwise runs in one. It is also unnecessary: of the
files ``ruff`` walked and did not report, the only ones that can still hide a
candidate are the ones it exempts by decorator, and :func:`may_be_rule_exempt`
rules those out by inspection. The gate combines that with ``ruff``'s own list
of walked files to skip the ~80% of the tree that provably holds nothing.

The parameter count here mirrors what ``PLR0913`` counts: positional-only plus
ordinary plus keyword-only, excluding ``*args`` / ``**kwargs``, and excluding
the leading ``self`` / ``cls`` of a method that is not a ``@staticmethod``.
Validated against the whole tree: 171 candidates found here versus 168
diagnostics from ``ruff``, differing by exactly the three ``@override`` methods
and by nothing else in either direction.
"""

import ast
import contextlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

_STATIC_METHOD: Final[str] = "staticmethod"
_OVERRIDE: Final[str] = "override"
_ARG_RULE: Final[str] = "PLR0913"
_POSITIONAL_RULE: Final[str] = "PLR0917"


class SiteStatus(StrEnum):
    """How an over-cap function currently stands with respect to ``ruff``.

    Named for the site rather than for the suppression because one member,
    :attr:`RULE_EXEMPT`, describes a function that is not suppressed at all:
    ``ruff`` simply declines to report it.
    """

    UNSUPPRESSED = "unsuppressed"
    PER_LINE = "per-line"
    BLANKET = "blanket"
    RULE_EXEMPT = "rule-exempt"


@dataclass(frozen=True)
class Candidate:
    """One function whose parameter count exceeds the cap.

    Derived from the AST alone, before ``ruff`` has had any say.
    """

    rel: str
    lineno: int
    qualname: str
    arg_count: int
    positional_count: int
    over_arg_cap: bool
    over_positional_cap: bool

    def __post_init__(self) -> None:
        """Reject coordinates the walker can never legally produce.

        Surfaces a walk bug immediately rather than letting an invalid key
        reach the baseline, where it would only fail much later on round-trip.

        Raises:
            ValueError: If ``rel`` or ``qualname`` is empty, ``lineno`` < 1, or
                ``arg_count`` < 1.
        """
        if not self.rel:
            msg = "rel must not be empty"
            raise ValueError(msg)
        if not self.qualname:
            msg = f"{self.rel}:{self.lineno}: qualname must not be empty"
            raise ValueError(msg)
        if self.lineno < 1:
            msg = f"lineno must be at least 1, got {self.lineno}"
            raise ValueError(msg)
        if self.arg_count < 1:
            msg = f"arg_count must be at least 1, got {self.arg_count}"
            raise ValueError(msg)
        if self.positional_count < 0:
            msg = f"positional_count must not be negative, got {self.positional_count}"
            raise ValueError(msg)
        if not (self.over_arg_cap or self.over_positional_cap):
            msg = f"{self.rel}:{self.lineno}: a candidate must breach a cap"
            raise ValueError(msg)

    @property
    def key(self) -> str:
        """Return the ``path::qualname::arity`` baseline identity.

        The qualified name rather than a line number because this list is
        long-lived: a ``path:lineno:col`` key would go stale on any unrelated
        edit above the marker, turning every neighbouring PR into a baseline
        regeneration.

        The arity is what stops an approved entry from being reused by a
        different function. Without it, deleting a baselined function and
        writing an unrelated one under the same name in the same file inherits
        the old authorisation silently, and an already-approved function can
        grow from six parameters to sixty with no baseline diff and no fresh
        review. Widening a suppressed signature should cost an approval.
        """
        return f"{self.rel}::{self.qualname}::{self.arg_count}"


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return the bare names of every decorator on *node*.

    Attribute and call forms are reduced to the final attribute, so
    ``@typing.override``, ``@override`` and ``@functools.cache(...)`` all yield
    the name the caller wants to test against.

    Returns:
        One name per decorator, in source order.
    """
    names: list[str] = []
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.append(target.attr)
        else:
            names.append(getattr(target, "id", ""))
    return names


def count_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    is_method: bool,
) -> tuple[int, int]:
    """Return the counts ``PLR0913`` and ``PLR0917`` would compute for *node*.

    Args:
        node: The function or method definition.
        is_method: Whether *node* is defined directly in a class body.

    Returns:
        A ``(total, positional)`` pair. ``total`` is positional-only plus
        ordinary plus keyword-only; ``positional`` drops the keyword-only
        tail. Both exclude ``*args`` / ``**kwargs`` and the leading ``self``
        / ``cls`` of a method that is not a ``@staticmethod``.
    """
    args = node.args
    positional = len(args.posonlyargs) + len(args.args)
    binds_receiver = is_method and _STATIC_METHOD not in _decorator_names(node)
    if binds_receiver and positional:
        positional -= 1
    return (positional + len(args.kwonlyargs), positional)


def is_rule_exempt(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return whether ``ruff`` exempts *node* from ``PLR0913`` by decorator.

    Returns:
        ``True`` for a function carrying ``@override`` / ``@typing.override``,
        which ``ruff`` skips syntactically without checking that the override
        is real.
    """
    return _OVERRIDE in _decorator_names(node)


def may_be_rule_exempt(raw: bytes) -> bool:
    """Return whether *raw* could possibly contain a decorator-exempt function.

    A cheap pre-filter for the one population the gate's neutralised ``ruff``
    pass cannot report for itself. It over-approximates on purpose: a ``False``
    must mean the file provably holds no exemption, because the caller skips
    parsing on the strength of it.

    Sound for the aliased form too. ``ruff`` resolves the decorator
    semantically, so ``from typing import override as _o`` followed by ``@_o``
    is exempt to ``ruff`` even though :func:`is_rule_exempt` matches only the
    bare final name. Scanning the raw bytes catches it anyway, because the
    import that binds the alias has to spell the original out.

    Args:
        raw: The undecoded file contents. Bytes rather than text so the check
            costs a read and a substring scan, with no decode.

    Returns:
        ``True`` when the file mentions the exempting decorator anywhere.
    """
    return _OVERRIDE.encode() in raw


def _statement_blocks(stmt: ast.stmt) -> Iterator[list[ast.stmt]]:
    """Yield the nested statement blocks of *stmt*.

    Every field a statement can hang a suite off: the ``if`` / ``for`` /
    ``while`` / ``with`` bodies and their ``else``, a ``try``'s handlers and
    ``finally``, and a ``match``'s cases.

    Yields:
        Each nested block, in no particular order.
    """
    for field in ("body", "orelse", "finalbody"):
        block = getattr(stmt, field, None)
        if isinstance(block, list):
            yield block
    for handler in getattr(stmt, "handlers", []):
        yield handler.body
    for case in getattr(stmt, "cases", []):
        yield case.body


class _CandidateWalker:
    """Collects over-cap function definitions with their qualified names.

    Walks statement blocks directly rather than subclassing
    :class:`ast.NodeVisitor`, whose ``generic_visit`` descends into every
    expression node in the file. A ``def`` is a statement and can only appear
    in a suite, so the millions of expression nodes in a large tree are all
    dead weight; skipping them is several seconds across the repository.
    """

    def __init__(self, rel: str, arg_cap: int, positional_cap: int) -> None:
        self._rel = rel
        self._arg_cap = arg_cap
        self._positional_cap = positional_cap
        self._stack: list[str] = []
        self._in_class: list[bool] = [False]
        self.candidates: list[Candidate] = []
        self.exempt_lines: set[int] = set()

    @contextlib.contextmanager
    def _scope(self, name: str, *, in_class: bool) -> Iterator[None]:
        """Push *name* for the duration of the block.

        A context manager rather than a hand-matched append/pop pair so the
        stack cannot desync if a visitor method grows an early return or a
        raise, which would otherwise corrupt every qualified name after it.

        Yields:
            ``None``; the scope is the point.
        """
        self._stack.append(name)
        self._in_class.append(in_class)
        try:
            yield
        finally:
            self._in_class.pop()
            self._stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_method = self._in_class[-1]
        with self._scope(node.name, in_class=False):
            total, positional = count_parameters(node, is_method=is_method)
            over_args = total > self._arg_cap
            over_positional = positional > self._positional_cap
            if over_args or over_positional:
                self.candidates.append(
                    Candidate(
                        rel=self._rel,
                        lineno=node.lineno,
                        qualname=".".join(self._stack),
                        arg_count=total,
                        positional_count=positional,
                        over_arg_cap=over_args,
                        over_positional_cap=over_positional,
                    )
                )
                if is_rule_exempt(node):
                    self.exempt_lines.add(node.lineno)
            self.walk(node.body)

    def walk(self, body: list[ast.stmt]) -> None:
        """Record every function definition reachable from *body*.

        A plain statement keeps the surrounding scope: a ``def`` guarded by
        ``if TYPE_CHECKING:`` inside a class body is still a method, so only
        a class or a function pushes a new frame.

        Args:
            body: The suite to walk.
        """
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                with self._scope(stmt.name, in_class=True):
                    self.walk(stmt.body)
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                self._visit_function(stmt)
            else:
                for block in _statement_blocks(stmt):
                    self.walk(block)


_FILE_LEVEL_DIRECTIVE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*#\s*ruff\s*:\s*(?i:noqa)\b(?P<codes>[^#]*)",
)


def has_file_level_blanket(text: str, rule: str) -> bool:
    """Return whether a file-level ``# ruff: noqa`` covers *rule*.

    Detected textually rather than by asking ruff, because this is the one
    suppression shape the gate bans outright and it must be recognised the
    same way whether or not ruff happens to report the function. A bare
    directive with no codes covers everything; a code list covers *rule* only
    when it names it (or a prefix of it).

    Returns:
        ``True`` when a blanket directive in *text* suppresses *rule*.
    """
    for line in text.splitlines():
        match = _FILE_LEVEL_DIRECTIVE_RE.match(line)
        if match is None:
            continue
        codes = match.group("codes").lstrip(": ").strip()
        if not codes:
            return True
        if any(
            rule.startswith(code.strip()) for code in codes.split(",") if code.strip()
        ):
            return True
    return False


@dataclass(frozen=True)
class FileScan:
    """Everything one source file contributes to the scan."""

    candidates: tuple[Candidate, ...]
    lines: tuple[str, ...]
    exempt_lines: frozenset[int]
    has_blanket: bool


def scan_source(
    rel: str,
    text: str,
    tree: ast.Module,
    arg_cap: int,
    positional_cap: int,
) -> FileScan:
    """Return the over-cap candidates in one already-parsed source file.

    Args:
        rel: POSIX path relative to the project root, used in candidate keys.
        text: The file contents, retained so the caller can read marker lines.
        tree: The parsed module.
        arg_cap: The ``max-args`` ceiling a candidate may exceed.
        positional_cap: The ``max-positional-args`` ceiling a candidate may
            exceed. Breaching either one makes a function a candidate: a
            framework-shaped signature that stays under the total cap can
            still carry a wide positional surface, which is the half that
            lets two same-typed arguments swap silently.

    Returns:
        The candidates, the physical lines, and the line numbers ``ruff``
        exempts by decorator.
    """
    walker = _CandidateWalker(rel, arg_cap, positional_cap)
    walker.walk(tree.body)
    return FileScan(
        candidates=tuple(walker.candidates),
        lines=tuple(text.splitlines()),
        exempt_lines=frozenset(walker.exempt_lines),
        has_blanket=has_file_level_blanket(text, _ARG_RULE)
        or has_file_level_blanket(text, _POSITIONAL_RULE),
    )


def find_nested_ruff_configs(project_root: Path, tracked: list[str]) -> list[str]:
    """Return tracked ruff config files that are not the root ``pyproject.toml``.

    ``ruff`` resolves configuration per directory, walking up from each linted
    file to the nearest ``ruff.toml`` / ``.ruff.toml`` / ``pyproject.toml``. A
    config in a subdirectory is therefore authoritative for everything beneath
    it, and the default ``select`` does not include the pylint family at all,
    so a nested config need not even mention ``max-args`` to disable this rule
    for its subtree.

    Args:
        project_root: The directory whose ``pyproject.toml`` is the real root.
        tracked: Every tracked path, POSIX-relative to *project_root*.

    Returns:
        The offending relative paths, sorted.
    """
    del project_root
    names = {"ruff.toml", ".ruff.toml", "pyproject.toml"}
    return sorted(
        rel for rel in tracked if Path(rel).name in names and Path(rel).parent != Path()
    )
