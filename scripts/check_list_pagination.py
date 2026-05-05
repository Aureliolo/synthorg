#!/usr/bin/env python3
"""Pre-push gate: forbid unbounded ``list_*`` / ``query`` repository methods.

A repository ``list_users``/``query`` method that returns every row in the
table is a DoS vector: any caller that triggers it (an unauth'd endpoint,
a debug script, an integration test) materialises an arbitrarily-large
tuple in memory. The CRUD vocabulary in
``docs/reference/conventions.md`` section 14 mandates ``limit`` (offset or
cursor) on every ``list_items`` and ``query``; this gate prevents
regression of that convention.

Scope:

* Files: every ``.py`` under ``src/synthorg/persistence/``. The gate
  naturally only flags class methods whose name matches the CRUD
  vocabulary, so non-repo files (``factory.py``, ``config.py``, ...)
  contribute zero overhead.
* Method names: ``list_<X>``, ``query`` (bare), or ``query_<X>``.
  Names with a leading underscore (``_list_internal``) are private
  helpers, not list endpoints, and are skipped.
* Async (``async def``) and sync (``def``) handled identically.

Per-method classification:

* PASS -- ``limit`` parameter present, no default value.
* PASS -- ``limit`` present with a numeric ``ast.Constant`` default.
* PASS -- ``limit`` default is ``None`` AND another parameter named
  ``cursor`` / ``offset`` / ``after_id`` / ``before_id`` exists
  (cursor-pagination is the alternative to a numeric default).
* PASS -- ``limit`` is missing AND a required parameter's annotation is
  a Sized-sequence generic (``Sequence[X]`` / ``list[X]`` / ``tuple[X,
  ...]`` / ``set[X]`` / ``frozenset[X]`` / ``Collection[X]``). The
  cardinality of the input sequence bounds the result set the same way
  ``limit`` does, so a ``WHERE col IN (...)``-style query is
  intrinsically bounded. ``Iterable[X]`` is intentionally excluded
  here -- the protocol only requires ``__iter__`` (not ``__len__``),
  so an arbitrary generator can satisfy it and the gate would then
  certify an unbounded scan as compliant.
* FAIL ``nullable-limit-no-cursor`` -- ``limit`` default is ``None``
  with no cursor sibling.
* FAIL ``missing-limit-param`` -- no ``limit`` parameter at all and
  no Sequence-bounded filter.
* FAIL ``invalid-limit-annotation`` -- ``limit`` parameter is present
  but its annotation is missing or is not ``int`` / ``int | None`` /
  ``Optional[int]`` (e.g. ``limit: str``, ``limit: object = 100``).
  A non-int ``limit`` is just as unbounded as no limit at all.

Known signature loophole (intentional, narrow):

* ``limit: int = SOME_NON_LITERAL`` -- when the default expression is
  neither a numeric literal nor ``None`` (an enum, a settings call, a
  module-level constant), the gate treats it as compliant. The
  parameter is present and named ``limit``; the gate's job is to catch
  the unbounded shape, not to police the default's runtime value. If
  ``SOME_NON_LITERAL`` resolves to an unbounded sentinel at runtime,
  that's a defect at the call site that this signature gate cannot
  detect; runtime validation belongs in the repository's own input
  guards, not here.

Allowlist
---------

``scripts/list_pagination_baseline.txt`` is a frozen list of
``<rel>:<class>.<method>:<reason>`` entries the gate ignores. The
baseline captures pre-existing offenders so the gate can ship without
forcing the matching persistence-layer fixes in the same PR. New
offenders cannot be added silently: ``--update`` rewrites the baseline,
and the rewritten file must be committed for the new entry to be
allowed. Baseline shrinkage is enforced -- an entry that no longer
matches a current violation is reported as ``baseline-stale``.

Per-line opt-out
----------------

Append ``# lint-allow: list-pagination -- <reason>`` to a method's
``def`` line to bypass the gate at that exact site (rare; rule of
thumb: prefer fixing the signature). The marker is honoured only when
it appears inside the line's actual comment (after an unquoted ``#``);
the same string inside a default value or docstring does not silence
the violation.

Usage
-----

    python scripts/check_list_pagination.py <file>...   # pre-push
    python scripts/check_list_pagination.py --scan-all  # CI
    python scripts/check_list_pagination.py --update    # regenerate baseline
"""

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PERSISTENCE_ROOT = _REPO_ROOT / "src" / "synthorg" / "persistence"
_BASELINE_PATH = _REPO_ROOT / "scripts" / "list_pagination_baseline.txt"

_OPT_OUT_MARKER = "lint-allow: list-pagination"

# Parameter names that, alongside a nullable ``limit``, indicate
# cursor / offset pagination. A method with ``limit: int | None = None``
# is unbounded UNLESS one of these siblings is present.
_CURSOR_PARAM_NAMES: frozenset[str] = frozenset(
    {"cursor", "offset", "after_id", "before_id"}
)

# Sized-sequence generic annotations that bound the result set by the
# cardinality of the input. A method with no ``limit`` but a required
# ``ids: Sequence[str]`` parameter is intrinsically bounded. Note
# ``Iterable`` is deliberately excluded: it only requires ``__iter__``,
# not ``__len__``, so a generator can satisfy it and the gate would
# then certify an unbounded scan as compliant.
_SEQUENCE_ANNOTATION_NAMES: frozenset[str] = frozenset(
    {"Sequence", "list", "tuple", "set", "frozenset", "Collection"}
)

_REASON_MISSING = "missing-limit-param"
_REASON_NULLABLE = "nullable-limit-no-cursor"
_REASON_INVALID_ANNOTATION = "invalid-limit-annotation"

_BASELINE_HEADER = """\
# Frozen baseline of pre-existing list_*/query repository methods missing
# required `limit` pagination. Each line is
# `<posix-path>:<class>.<method>:<reason>` sorted in deterministic order.
#
# Reasons:
#   missing-limit-param      -- no `limit` parameter exists
#   nullable-limit-no-cursor -- limit default is None and no cursor/offset sibling
#   invalid-limit-annotation -- `limit` annotation is missing or not int/int|None
#
# scripts/check_list_pagination.py reads this file to suppress violations
# at these exact sites. New offenders NOT in this list will fail the
# pre-push hook. An entry here that no longer matches a real offender
# (method renamed, fixed, deleted) is reported as `baseline-stale` so
# the file must be regenerated when shrinkage is genuine.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_list_pagination.py --update
"""


class InspectionError(RuntimeError):
    """A source file could not be parsed for AST inspection."""


class _Violation(NamedTuple):
    class_name: str
    method_name: str
    reason: str
    lineno: int


def _is_target_method_name(name: str) -> bool:
    """Return True for public ``list_<X>`` / ``query`` / ``query_<X>`` names."""
    if name.startswith("_"):
        return False
    if name == "query":
        return True
    return name.startswith(("list_", "query_"))


def _params_with_defaults(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, ast.expr | None]:
    """Map every formal parameter name to its default expression (or ``None``).

    The map covers positional-or-keyword params, kw-only params, and the
    ``self`` placeholder; ``*args`` and ``**kwargs`` collectors are
    skipped because a spec couldn't reasonably name a positional rest.

    Defaults align with their parameters from the right; the ``defaults``
    list is shorter than ``args`` exactly when the leading args are
    required. Mirroring CPython's own slot-resolution keeps the math
    correct without a custom alignment routine.
    """
    args = func.args
    out: dict[str, ast.expr | None] = {}
    pos = list(args.posonlyargs) + list(args.args)
    pos_defaults: list[ast.expr | None] = [None] * (
        len(pos) - len(args.defaults)
    ) + list(args.defaults)
    for arg, default in zip(pos, pos_defaults, strict=True):
        out[arg.arg] = default
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        out[arg.arg] = default
    return out


def _is_literal_none(value: ast.expr | None) -> bool:
    """Return True if ``value`` is the literal ``None`` constant."""
    return isinstance(value, ast.Constant) and value.value is None


def _is_numeric_constant(value: ast.expr | None) -> bool:
    """Return True if ``value`` is a numeric ``ast.Constant`` (int/float)."""
    return isinstance(value, ast.Constant) and isinstance(value.value, (int, float))


def _annotated_first_arg(annotation: ast.Subscript) -> ast.expr:
    """Return the first slice element of ``Annotated[T, ...]``.

    Caller has already verified the outer name is ``Annotated``. PEP 593
    requires at least two arguments (``T`` plus a metadata entry), so
    ``ast.Tuple`` is the expected shape; CPython tolerates a single-arg
    ``Annotated[T]`` though, so we handle the bare-``slice`` fallback.
    """
    inner = annotation.slice
    if isinstance(inner, ast.Tuple) and inner.elts:
        return inner.elts[0]
    return inner


def _int_subscript_inner(annotation: ast.Subscript) -> ast.expr | None:
    """Return the type ``Optional[T]`` / ``Annotated[T, ...]`` wraps, else None.

    A subscript whose outer name is anything other than ``Optional`` or
    ``Annotated`` does not narrow to ``int`` and the caller rejects.
    """
    outer = _annotation_root_name(annotation.value)
    if outer == "Optional":
        return annotation.slice
    if outer == "Annotated":
        return _annotated_first_arg(annotation)
    return None


def _is_none_annotation(annotation: ast.expr | None) -> bool:
    """Return True if *annotation* is the literal ``None`` constant."""
    return isinstance(annotation, ast.Constant) and annotation.value is None


def _is_int_or_none_binop(annotation: ast.BinOp) -> bool:
    """Return True for ``int | None`` / ``None | int`` BinOp shapes only.

    Accepts the two permutations of an ``int``-with-``None`` union; any
    other combination (``int | str``, ``None | None``, ``str | None``,
    nested unions ending elsewhere) returns False so the caller rejects.
    """
    left, right = annotation.left, annotation.right
    return (_is_int_annotation(left) and _is_none_annotation(right)) or (
        _is_none_annotation(left) and _is_int_annotation(right)
    )


def _is_int_annotation(annotation: ast.expr | None) -> bool:
    """Return True if *annotation* permits only ``int`` or ``int | None``.

    Accepted shapes:

    * ``int`` (bare ``ast.Name``).
    * ``int | None`` / ``None | int`` (``ast.BinOp`` over ``BitOr``,
      with one operand ``int`` and the other literally ``None``).
    * ``Optional[int]`` (``ast.Subscript`` over ``Optional``).
    * ``Annotated[int, ...]`` / ``Annotated[int | None, ...]`` (PEP 593
      metadata wrapper; the gate looks through the wrapper at the first
      slice element).

    Standalone ``None`` is NOT a valid ``limit`` type; it only counts as
    valid when paired with ``int`` inside a union. A missing annotation,
    a non-``int`` base type (``str`` / ``object`` / ...), or a union
    containing a non-``int``/``None`` member returns ``False`` -- the
    caller treats that as ``invalid-limit-annotation``.
    """
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id == "int"
    if isinstance(annotation, ast.Constant):
        return False
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _is_int_or_none_binop(annotation)
    if isinstance(annotation, ast.Subscript):
        return _is_int_annotation(_int_subscript_inner(annotation))
    return False


def _find_arg(
    func: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> ast.arg | None:
    """Return the ``ast.arg`` for parameter *name* on *func*, or ``None``.

    Searches positional-only, positional-or-keyword, and keyword-only
    parameters; ``*args`` / ``**kwargs`` collectors are out of scope
    because the gate's vocabulary names a concrete parameter.
    """
    args = func.args
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        if arg.arg == name:
            return arg
    return None


def _annotation_root_name(annotation: ast.expr | None) -> str | None:
    """Return the outermost identifier of *annotation*, or ``None``.

    Handles ``Sequence[str]`` (Subscript over Name), ``list[str]``,
    ``collections.abc.Sequence[str]`` (Subscript over Attribute), bare
    ``Sequence``, and ``Annotated[Sequence[str], ...]`` (PEP 593, the
    Pydantic / metadata-bearing shape) by recursing into the first
    argument of ``Annotated[...]``. Anything that is not a Name,
    Attribute, or Subscript wrapping one of those returns ``None``.
    """
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        outer = _annotation_root_name(annotation.value)
        if outer != "Annotated":
            return outer
        return _annotation_root_name(_annotated_first_arg(annotation))
    return None


def _has_required_sequence_param(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return True if *func* has a required Sequence-typed parameter.

    A Sequence-typed REQUIRED parameter (no default) bounds the result
    set the same way a ``limit`` does: a ``WHERE col IN (...)``-style
    query is intrinsically bounded by the cardinality of the input.
    Optional Sequence parameters (default ``None``) do not count
    because the method behaves unbounded when the caller omits them.
    """
    args = func.args
    pos = list(args.posonlyargs) + list(args.args)
    pos_defaults: list[ast.expr | None] = [None] * (
        len(pos) - len(args.defaults)
    ) + list(args.defaults)
    candidates: list[tuple[ast.arg, ast.expr | None]] = list(
        zip(pos, pos_defaults, strict=True)
    ) + list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
    for arg, default in candidates:
        if default is not None:
            continue
        root = _annotation_root_name(arg.annotation)
        if root in _SEQUENCE_ANNOTATION_NAMES:
            return True
    return False


def _classify_method(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """Return a violation reason for *func*, or ``None`` if it's compliant.

    Compliance shapes (any one is enough):

    * ``limit`` parameter missing AND a required Sequence-typed filter is
      present (cardinality of the input bounds the result set).
    * ``limit`` is annotated as ``int`` / ``int | None`` /
      ``Optional[int]`` AND one of:
        * required (no default), OR
        * has a numeric constant default, OR
        * defaults to ``None`` AND a cursor sibling exists, OR
        * has a non-literal default (enum / module constant) -- the
          "Known signature loophole" in the module docstring.

    A ``limit`` parameter whose annotation is missing or not an
    ``int``-compatible shape (``str``, ``object``, ``Any``, etc.)
    returns ``invalid-limit-annotation``: the gate's contract is
    ``limit: int``, and a parameter merely *named* limit with the wrong
    type is just as unbounded as no limit at all.
    """
    params = _params_with_defaults(func)
    if "limit" not in params:
        return None if _has_required_sequence_param(func) else _REASON_MISSING
    limit_arg = _find_arg(func, "limit")
    if limit_arg is None or not _is_int_annotation(limit_arg.annotation):
        return _REASON_INVALID_ANNOTATION
    default = params["limit"]
    if default is None or _is_numeric_constant(default):
        return None
    if _is_literal_none(default):
        cursor_sibling = any(name in params for name in _CURSOR_PARAM_NAMES)
        return None if cursor_sibling else _REASON_NULLABLE
    return None


def _iter_class_methods(
    cls: ast.ClassDef,
) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield direct ``def`` / ``async def`` children of *cls*."""
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _comment_text(line: str) -> str:
    """Return the comment portion of *line* (text after an unquoted ``#``).

    Walks the line tracking single- and double-quote string state so a
    ``#`` inside a string literal does not start a comment. This is
    deliberately a lightweight character scanner rather than ``tokenize``
    because it runs on a single line per call -- spinning up the full
    tokenizer per def-line would dominate scan time.

    Triple-quoted strings, f-string expressions, and multi-line strings
    crossing the def line are out of scope: the marker check operates on
    the single ``def`` line, and a real-world ``def`` with an embedded
    triple-quoted default is rare enough that we accept a false negative
    over the cost of full token-stream parsing.
    """
    in_quote: str | None = None
    escaped = False
    for i, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if in_quote is not None:
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            continue
        if ch == "#":
            return line[i:]
    return ""


def _line_has_opt_out(source_lines: list[str], lineno: int) -> bool:
    """Return True if the line's comment carries the per-line opt-out marker."""
    if not 1 <= lineno <= len(source_lines):
        return False
    return _OPT_OUT_MARKER in _comment_text(source_lines[lineno - 1])


def _scan_file(path: Path, rel: str) -> list[_Violation]:
    """Return ``_Violation`` records for every flagged method in *path*.

    Walks every top-level ``ClassDef`` (nested classes are out of scope --
    persistence files don't use them) and inspects each direct method
    whose name matches the CRUD vocabulary. Per-line opt-out markers
    on the ``def`` line suppress emission.

    The *rel* argument is plumbed through purely for error messages on
    parse failure; it does not affect the returned tuples.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        msg = f"failed to read {rel}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        msg = f"failed to parse {rel}: SyntaxError at line {exc.lineno}: {exc.msg}"
        raise InspectionError(msg) from exc
    source_lines = source.splitlines()
    violations: list[_Violation] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for method in _iter_class_methods(node):
            if not _is_target_method_name(method.name):
                continue
            reason = _classify_method(method)
            if reason is None:
                continue
            if _line_has_opt_out(source_lines, method.lineno):
                continue
            violations.append(_Violation(node.name, method.name, reason, method.lineno))
    return violations


def _format_entry(rel: str, class_name: str, method_name: str, reason: str) -> str:
    """Render the canonical baseline / violation line."""
    return f"{rel}:{class_name}.{method_name}:{reason}"


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable violation messages."""
    return path.resolve().relative_to(_REPO_ROOT.resolve()).as_posix()


_BASELINE_ENTRY_PATTERN = re.compile(
    r"^[^:]+:[A-Za-z_][\w]*\.[A-Za-z_][\w]*:[a-z][a-z\-]*$"
)


def _load_baseline() -> set[str]:
    """Return the set of allowlisted ``<rel>:<class>.<method>:<reason>`` entries.

    Validates each non-empty, non-comment line against the canonical
    shape and rejects duplicates. A corrupted baseline that silently
    drops entries would let real offenders slip past the gate.
    """
    if not _BASELINE_PATH.exists():
        return set()
    entries: set[str] = set()
    errors: list[str] = []
    try:
        rel_path = _rel(_BASELINE_PATH)
    except ValueError:
        rel_path = str(_BASELINE_PATH)
    for lineno, line in enumerate(
        _BASELINE_PATH.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _BASELINE_ENTRY_PATTERN.match(stripped):
            errors.append(
                f"{rel_path}:{lineno}: malformed entry "
                f"(expected '<rel>:<class>.<method>:<reason>', got {stripped!r})"
            )
            continue
        if stripped in entries:
            errors.append(f"{rel_path}:{lineno}: duplicate entry {stripped!r}")
            continue
        entries.add(stripped)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        plural = "s" if len(errors) != 1 else ""
        msg = (
            f"{rel_path}: baseline failed validation "
            f"({len(errors)} error{plural}; first: {errors[0]}); "
            "regenerate with 'uv run python scripts/check_list_pagination.py "
            "--update' or fix by hand."
        )
        raise ValueError(msg)
    return entries


def _iter_persistence_files() -> Iterator[Path]:
    """Yield every ``.py`` file under ``_PERSISTENCE_ROOT`` (sorted, no dunders).

    ``__init__.py`` is included even though it rarely contains repo
    classes; the AST walker skips it cheaply when there's nothing to
    flag, and excluding it would require special-casing in three places.
    Any ``__pycache__`` directories are skipped because they aren't
    source.
    """
    for path in sorted(_PERSISTENCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _scan(path: Path, baseline: set[str]) -> tuple[list[str], set[str]]:
    """Scan *path*, return ``(violations, hit_baseline_entries)``.

    Splitting the return into "violations to report" and "baseline
    entries that suppressed something" lets ``cmd_scan_all`` compute
    ``baseline - hits`` to detect stale (shrinkage-eligible) entries
    after every file is processed.

    Re-raises :class:`InspectionError` so callers can distinguish a
    genuine pagination violation (exit 1) from a parse failure (exit 2);
    flattening parse errors into the violation stream would let a
    syntax-broken file silently disable the gate's coverage there.
    """
    rel = _rel(path)
    scan_hits = _scan_file(path, rel)
    violations: list[str] = []
    hits: set[str] = set()
    for hit in scan_hits:
        entry = _format_entry(rel, hit.class_name, hit.method_name, hit.reason)
        if entry in baseline:
            hits.add(entry)
            continue
        violations.append(entry)
    return violations, hits


def _scan_all_for_baseline() -> list[str]:
    """Return every offender in the persistence tree for baseline regeneration.

    Re-raises ``InspectionError`` instead of silently continuing on a
    parse failure: a baseline that quietly skips an unparseable file
    would let the gate suppress every offender in that file going
    forward, exactly the silent-failure shape the gate exists to
    prevent.
    """
    entries: list[str] = []
    for path in _iter_persistence_files():
        rel = _rel(path)
        entries.extend(
            _format_entry(rel, hit.class_name, hit.method_name, hit.reason)
            for hit in _scan_file(path, rel)
        )
    return sorted(entries)


def cmd_update() -> int:
    """Regenerate the baseline file from the current tree state."""
    entries = _scan_all_for_baseline()
    body = _BASELINE_HEADER + "\n".join(entries) + "\n"
    _BASELINE_PATH.write_text(body, encoding="utf-8")
    print(
        f"Wrote {len(entries)} entries to {_rel(_BASELINE_PATH)}.",
        file=sys.stderr,
    )
    return 0


def cmd_scan_all() -> int:
    """Scan every persistence file (CI mode) and enforce baseline shrinkage."""
    baseline = _load_baseline()
    violations: list[str] = []
    matched: set[str] = set()
    inspection_errors: list[str] = []
    for path in _iter_persistence_files():
        try:
            v, h = _scan(path, baseline)
        except InspectionError as exc:
            inspection_errors.append(str(exc))
            continue
        violations.extend(v)
        matched |= h
    if inspection_errors:
        for err in inspection_errors:
            print(f"check_list_pagination: {err}", file=sys.stderr)
        return 2
    stale = sorted(baseline - matched)
    return _report(violations, stale)


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the supplied paths (pre-commit / pre-push entry point).

    Files outside ``_PERSISTENCE_ROOT`` are skipped because the gate's
    contract is repository-only; mistakenly running it across
    ``src/synthorg/api/`` should be a no-op rather than crash.

    Shrinkage detection is intentionally *not* run in this entry point:
    pre-commit / pre-push receive only the changed files, so a baseline
    entry that no longer matches because its file wasn't in the diff
    would be a false positive. ``cmd_scan_all`` is the canonical place
    to enforce shrinkage.

    A parse failure on any scanned file exits 2 (consistent with
    ``cmd_scan_all``); the developer's local push fails loudly rather
    than rolling a syntax error into the violation stream.
    """
    baseline = _load_baseline()
    persistence_root = _PERSISTENCE_ROOT.resolve()
    violations: list[str] = []
    inspection_errors: list[str] = []
    for raw in paths:
        path = Path(raw).resolve()
        if not path.exists() or path.suffix != ".py":
            continue
        if not path.is_relative_to(persistence_root):
            continue
        try:
            v, _ = _scan(path, baseline)
        except InspectionError as exc:
            inspection_errors.append(str(exc))
            continue
        violations.extend(v)
    if inspection_errors:
        for err in inspection_errors:
            print(f"check_list_pagination: {err}", file=sys.stderr)
        return 2
    return _report(violations, stale=[])


def _report(violations: list[str], stale: list[str]) -> int:
    """Print findings and return a pre-push-friendly exit code."""
    if not violations and not stale:
        return 0
    for line in violations:
        print(line)
    for entry in stale:
        print(f"baseline-stale: {entry}")
    print(
        "\nUnbounded list_*/query repository methods are a DoS vector: a"
        "\ncaller that triggers them materialises an arbitrarily-large tuple."
        "\nConventions section 14 mandates `limit` (offset or cursor) on every"
        "\n`list_items`/`query`."
        "\n"
        "\nFix the signature, then either:"
        "\n  * rerun the gate (the entry will disappear naturally), OR"
        "\n  * regenerate the baseline if the change is shrinkage:"
        "\n      uv run python scripts/check_list_pagination.py --update"
        "\n"
        "\nFor a genuine fixed-set return, mark the def line with:"
        f"\n  # {_OPT_OUT_MARKER} -- <reason>",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Gate on list_*/query repository methods missing required `limit`."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit / pre-push supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan the full src/synthorg/persistence/ tree (CI mode).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Regenerate scripts/list_pagination_baseline.txt from current state.",
    )
    args = parser.parse_args(argv)

    try:
        if args.update:
            return cmd_update()
        if args.scan_all:
            return cmd_scan_all()
        return cmd_scan_paths(args.paths)
    except (InspectionError, ValueError) as exc:
        print(f"check_list_pagination: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
