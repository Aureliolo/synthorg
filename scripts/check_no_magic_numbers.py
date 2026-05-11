#!/usr/bin/env python3
"""Pre-push / CI gate: no bare numeric literals in business logic.

Enforces the rule: every numeric threshold / weight / limit / timeout /
policy in ``src/synthorg/`` is a setting registered in
``src/synthorg/settings/definitions/``, not a bare numeric literal in
business code. Settings flow through the canonical resolution chain
(DB > env > YAML > code default) so operators can tune without code
changes; bare literals freeze the value and bypass observability.

Detection
---------

Walks every ``.py`` file under ``src/synthorg/`` and flags numeric
literals appearing in two contexts:

1. **Module-level numeric constants**: ``FOO = 1024`` at module body
   level where the target is an :class:`ast.Name` and the value is an
   :class:`int` or :class:`float`. Matches both UPPER_SNAKE
   (``_GC_EVERY_N_ACQUIRES``) and lowercase identifiers.

2. **Default arguments**: ``def foo(timeout: float = 30.0)`` where the
   default is a numeric :class:`ast.Constant`. Catches the kwarg-default
   form that's the second-most-common shape of a hardcoded policy value.

Allowlist
---------

The following bare literals are NOT violations:

- ``0``, ``1``, ``-1``, ``0.0``, ``1.0``, ``-1.0`` -- sentinel /
  off-by-one / boolean-ish values.
- HTTP status codes -- only when the literal is the default of a
  function parameter named ``status_code`` or ``status``. The gate
  inspects function signatures, not call sites; ``Response(status_code=404)``
  passes because ``404`` is not a module-level assign or function
  default in the caller.
- Powers of 2 in ``{1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072}``
  -- only when the literal is the default of a function parameter named
  ``buffering``, ``buffer_size``, ``bufsize``, ``blocksize``, or
  ``block_size``. ``chunk_size`` is intentionally excluded: the name is
  generic enough that ``chunk_size=512`` may legitimately be a
  business-policy literal masquerading as an I/O size, so any
  ``chunk_size`` default registers as a magic number unless explicitly
  opted out. The gate does not scan call-site
  positional arguments to I/O methods such as ``read`` / ``recv`` /
  ``write``; literals at those sites either flow through a
  function-default that already satisfies the allowlist or carry a
  per-line ``# lint-allow:`` marker.
- Hex literals whose source spelling starts with ``0x`` -- conventional
  bit-mask form; the value itself is unrestricted because masks are
  the algorithm, not policy.
- Files under ``src/synthorg/settings/definitions/`` -- declaring
  numerics is the whole job of those modules.
- Files under ``src/synthorg/persistence/migrations/`` -- Atlas
  generates these.
- Files under ``src/synthorg/observability/events/`` -- event-name
  registries and version constants.
- Module-level annotated numeric constants of the form
  ``NAME: int|float|Final|Final[int]|Final[float] = literal`` -- the
  annotation declares the literal IS the named constant the rule wants
  to encourage, so flagging produces only noise. Bare ``NAME = literal``
  without an annotation still flags; the developer must opt in by
  typing the assignment.

Per-line opt-out
----------------

Append ``# lint-allow: magic-numbers -- <reason>`` to the offending
line. The justification after ``--`` is required and must be non-empty.
Mirrors the ``persistence-boundary`` and ``bootstrap-wiring`` markers.

Baseline
--------

``scripts/no_magic_numbers_baseline.txt`` lists pre-existing
``path:lineno:col`` sites that the gate ignores so the rule can ship
without forcing a one-shot mass migration. Site-by-site monotonic
shrink: a PR fails if it adds a site not in the baseline, even when
total count drops. Regenerate with ``--update``.

Usage::

    python scripts/check_no_magic_numbers.py            # CI / pre-push
    python scripts/check_no_magic_numbers.py --paths src/synthorg
    python scripts/check_no_magic_numbers.py --update   # regen baseline
"""

import argparse
import ast
import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Final, TypeGuard, cast

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_BASELINE_PATH: Final[Path] = _REPO_ROOT / "scripts" / "no_magic_numbers_baseline.txt"


def _baseline_path(project_root: Path) -> Path:
    """Return the baseline file location anchored at *project_root*.

    The baseline lives at ``<project_root>/scripts/no_magic_numbers_baseline.txt``
    so that ``--repo-root`` runs read and write the correct file rather
    than the one belonging to the checkout that contains this script.
    """
    return project_root / "scripts" / "no_magic_numbers_baseline.txt"


_SUPPRESSION_MARKER: Final[str] = "lint-allow: magic-numbers"

# ── Allowlists ──────────────────────────────────────────────────

# Bare literal values that never count as magic numbers regardless of
# context: integer 0/1/-1 (sentinels, off-by-one, booleans pretending
# to be ints) and their float counterparts (initial accumulator values).
# Equality membership in :func:`_TRIVIAL_VALUES` is value-based, so 0.0
# matches 0, 1.0 matches 1, and so on -- listing only the integer forms
# is sufficient and keeps the set free of duplicate-by-equality entries.
_TRIVIAL_VALUES: Final[frozenset[int]] = frozenset({0, 1, -1})

# I/O-buffer kwarg sites where powers-of-2 are conventional and treating
# them as policy would force every byte-stream read to register a
# setting. The gate matches by *parameter name* in function signatures
# whose default is a power-of-2; raw call-site arguments are not
# scanned (the gate only looks at module-level assigns and defaults).
# Locked by: test_io_default_allowlisted_all_kwargs_all_pow2 /
#            test_io_kwarg_with_non_pow2_still_flagged /
#            test_chunk_size_default_flags_at_all_pow2.
_IO_KEYWORD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "buffering",
        "buffer_size",
        "bufsize",
        "blocksize",
        "block_size",
    }
)
_IO_ALLOWED_POWERS_OF_2: Final[frozenset[int]] = frozenset(
    {1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072},
)

# HTTP status code allowlist context. The literal is exempt when:
#   - it's the value of a kwarg named ``status_code`` / ``status``
#   - it's the default of a parameter named ``status_code`` / ``status``
# Locked by: test_status_default_allowlisted_all_kwargs.
_HTTP_STATUS_KEYWORDS: Final[frozenset[str]] = frozenset({"status_code", "status"})

# Path-prefix allowlist for whole-file exemptions. Files under these
# prefixes are skipped entirely. POSIX-relative.
# Locked by: test_file_prefix_allowlist /
#            test_file_prefix_allowlist_does_not_match_substring.
_FILE_ALLOWLIST_PREFIXES: Final[tuple[str, ...]] = (
    "src/synthorg/settings/definitions/",
    "src/synthorg/persistence/migrations/",
    "src/synthorg/observability/events/",
)

# Module-level annotation shapes that mark a numeric named constant.
# Locked by: test_named_constant_allowlist_contents +
#            test_annotation_marks_as_named_constant_helper.
_NAMED_CONSTANT_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    {"int", "float", "Final"},
)
_NAMED_CONSTANT_FINAL_SLICES: Final[frozenset[str]] = frozenset(
    {"int", "float"},
)

# ── Helpers ─────────────────────────────────────────────────────


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments."""
    if repo_root is None:
        return _REPO_ROOT
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def _resolve_root(root: Path, project_root: Path) -> Path | None:
    """Return *root* resolved under *project_root*, or ``None`` if outside."""
    candidate = root if root.is_absolute() else project_root / root
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    project_root_str = os.fspath(project_root.resolve(strict=False))
    resolved_str = os.fspath(resolved)
    try:
        common = os.path.commonpath([project_root_str, resolved_str])
    except ValueError:
        return None
    if common != project_root_str:
        return None
    return resolved


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` file under *abs_root* as ``(abs, rel)``.

    Falls back to :meth:`Path.rglob` when ``git`` is unavailable or fails;
    the fallback widens scope to include untracked / gitignored files,
    so a stderr warning is emitted to make the semantic change visible
    rather than silently mutating what the gate scans.
    """
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", f"{rel_root}/*.py"],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(
            f"check_no_magic_numbers: git ls-files failed in "
            f"{project_root} ({type(exc).__name__}: {exc}); falling back "
            f"to rglob (scope widens to include untracked / gitignored "
            f"files).",
            file=sys.stderr,
        )
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _is_file_allowlisted(rel: str) -> bool:
    """Return True if *rel* is exempt from scanning entirely."""
    return any(rel.startswith(prefix) for prefix in _FILE_ALLOWLIST_PREFIXES)


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the suppression marker as a comment.

    The marker must be followed by ``--`` and non-empty justification
    text, mirroring ``check_persistence_boundary.py``.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if not comment.startswith(_SUPPRESSION_MARKER):
            continue
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--"):
            justification = suffix[2:].strip()
            if justification:
                return True
    return False


# ── Detection ───────────────────────────────────────────────────


def _is_numeric_constant(node: ast.expr) -> TypeGuard[ast.Constant]:
    """Return True iff *node* is an int/float :class:`ast.Constant`.

    Booleans are excluded -- ``True``/``False`` are :class:`ast.Constant`
    with ``isinstance(value, int)`` due to bool's int subclass quirk,
    but they are not magic numbers under any reading of the rule. The
    :class:`TypeGuard` return lets callers access ``node.value`` directly
    without re-checking the constant/non-bool invariant.
    """
    if not isinstance(node, ast.Constant):
        return False
    if isinstance(node.value, bool):
        return False
    return isinstance(node.value, (int, float))


def _unwrap_unary(node: ast.expr) -> tuple[ast.expr, bool]:
    """Return ``(inner_constant_node, is_negated)`` for ``-N`` / ``+N`` / ``N``.

    The AST stores ``-1`` as ``UnaryOp(USub, Constant(1))``, not
    ``Constant(-1)``. Callers test the unwrapped node for numeric-ness
    and use *is_negated* when computing the effective value.
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return node.operand, isinstance(node.op, ast.USub)
    return node, False


def _effective_value(node: ast.expr) -> float | None:
    """Return the numeric value of a (possibly unary-prefixed) constant."""
    inner, negated = _unwrap_unary(node)
    if not isinstance(inner, ast.Constant):
        return None
    value = inner.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return -value if negated else value


def _annotation_marks_as_named_constant(annotation: ast.expr | None) -> bool:
    """Return True iff *annotation* declares a numeric named constant.

    The gate treats an annotated module-level assignment as the
    developer's explicit declaration that the literal IS the named
    constant; an unannotated assignment is ambiguous (could be a
    one-time module-load computation) and continues to flag. Qualified
    forms like ``typing.Final[int]`` are deliberately not matched --
    direct ``Final`` imports are the project convention.
    """
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name) and annotation.id in _NAMED_CONSTANT_TYPE_NAMES:
        return True
    if not isinstance(annotation, ast.Subscript):
        return False
    if not isinstance(annotation.value, ast.Name):
        return False
    if annotation.value.id != "Final":
        return False
    slice_node = annotation.slice
    return (
        isinstance(slice_node, ast.Name)
        and slice_node.id in _NAMED_CONSTANT_FINAL_SLICES
    )


def _is_hex_literal(node: ast.expr, source_lines: list[str]) -> bool:
    """Return True iff *node*'s source spelling starts with ``0x``.

    Hex literals are conventional bit-mask form; the algorithm IS the
    constant. The raw source segment is consulted because :class:`ast`
    discards radix information on parse.
    """
    if not isinstance(node, ast.Constant) or not isinstance(node.value, int):
        return False
    line_idx = node.lineno - 1
    if line_idx < 0 or line_idx >= len(source_lines):
        return False
    line = source_lines[line_idx]
    col = node.col_offset
    if col < 0 or col + 2 > len(line):
        return False
    segment = line[col : col + 2].lower()
    return segment == "0x"


class _ParentTracker(ast.NodeTransformer):
    """Annotate every node with a ``.parent`` attribute, in place.

    Plain :class:`ast.NodeVisitor` carries no parent reference; the
    detection rules below need to look one level up to recognise
    "this constant is a kwarg value" / "this constant is the default
    of a parameter named ``status_code``". One pass of this transformer
    populates the link.
    """

    def __init__(self) -> None:
        super().__init__()
        self._parent: ast.AST | None = None

    def visit(self, node: ast.AST) -> ast.AST:
        node.parent = self._parent  # type: ignore[attr-defined]
        prev = self._parent
        self._parent = node
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._parent = prev
        return node


def _is_default_of_named_param(
    constant: ast.AST,
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
    param_names: set[str],
) -> bool:
    """Return True iff *constant* is the default of a parameter in *param_names*.

    ``args.defaults`` aligns RIGHT with ``args.args``: the i-th default
    corresponds to the (n-len(defaults)+i)-th positional arg.
    ``args.kw_defaults`` aligns ONE-TO-ONE with ``args.kwonlyargs`` and
    contains ``None`` for required kw-only params.
    """
    args = function_node.args
    positional = args.args
    pos_defaults = args.defaults
    if pos_defaults:
        offset = len(positional) - len(pos_defaults)
        for i, default in enumerate(pos_defaults):
            if default is constant and 0 <= offset + i < len(positional):
                return positional[offset + i].arg in param_names
    kwonly = args.kwonlyargs
    kw_defaults = args.kw_defaults
    for arg, kw_default in zip(kwonly, kw_defaults, strict=False):
        if kw_default is None:
            continue
        if kw_default is constant:
            return arg.arg in param_names
    return False


def _is_status_code_default(
    constant: ast.AST,
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    return _is_default_of_named_param(
        constant, function_node, set(_HTTP_STATUS_KEYWORDS)
    )


def _is_io_default(
    constant: ast.AST,
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
    value: float,
) -> bool:
    if not value.is_integer() or int(value) not in _IO_ALLOWED_POWERS_OF_2:
        return False
    return _is_default_of_named_param(constant, function_node, set(_IO_KEYWORD_NAMES))


# ── Scanner ─────────────────────────────────────────────────────


class _Hit:
    """One reportable site: ``(rel, lineno, col, kind, value)``."""

    __slots__ = ("col", "kind", "lineno", "rel", "value")

    def __init__(self, rel: str, lineno: int, col: int, kind: str, value: str) -> None:
        self.rel = rel
        self.lineno = lineno
        self.col = col
        self.kind = kind
        self.value = value

    def baseline_key(self) -> str:
        return f"{self.rel}:{self.lineno}:{self.col}"

    def message(self) -> str:
        return (
            f"{self.rel}:{self.lineno}:{self.col}: magic-numbers: "
            f"{self.kind} [{self.value}] -- migrate to "
            f"src/synthorg/settings/definitions/ or add "
            f"'# lint-allow: magic-numbers -- <reason>' on this line."
        )


class ScanError(Exception):
    """Raised when *file_path* cannot be read or parsed for scanning.

    Carries the relative path and a human-readable reason. Surfaced
    via :func:`main` as exit code 2 so the gate fails loud rather
    than silently letting an unscannable file through.
    """


def _scan_file(file_path: Path, rel: str) -> list[_Hit]:
    """Return every magic-number hit in *file_path* (no allowlist applied).

    Raises :class:`ScanError` when the file cannot be read (`OSError`,
    `UnicodeDecodeError`) or parsed (`SyntaxError`). Silent fallbacks
    would let a corrupted file or merge-conflict-mangled module hide
    a magic number behind an empty hit list -- the gate is load-bearing
    at pre-push, so it must surface scan failures rather than mask them.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{rel}: cannot read file ({type(exc).__name__}: {exc})"
        raise ScanError(msg) from exc
    try:
        tree = ast.parse(text, filename=str(file_path))
    except SyntaxError as exc:
        msg = f"{rel}: cannot parse file (SyntaxError at line {exc.lineno}: {exc.msg})"
        raise ScanError(msg) from exc
    _ParentTracker().visit(tree)
    source_lines = text.splitlines()
    hits: list[_Hit] = [
        *_collect_module_assign_hits(tree, rel, source_lines),
        *_collect_default_arg_hits(tree, rel, source_lines),
    ]
    return [hit for hit in hits if not _hit_is_suppressed(hit, source_lines)]


def _collect_module_assign_hits(
    tree: ast.Module, rel: str, source_lines: list[str]
) -> list[_Hit]:
    """Flag every module-level numeric assignment in *tree*."""
    hits: list[_Hit] = []
    for assign_node in tree.body:
        if isinstance(assign_node, ast.Assign):
            for target in assign_node.targets:
                if not isinstance(target, ast.Name):
                    continue
                hit = _classify_module_assign(
                    target.id,
                    assign_node.value,
                    None,
                    rel,
                    source_lines,
                )
                if hit is not None:
                    hits.append(hit)
        elif (
            isinstance(assign_node, ast.AnnAssign)
            and isinstance(assign_node.target, ast.Name)
            and assign_node.value is not None
        ):
            hit = _classify_module_assign(
                assign_node.target.id,
                assign_node.value,
                assign_node.annotation,
                rel,
                source_lines,
            )
            if hit is not None:
                hits.append(hit)
    return hits


def _collect_default_arg_hits(
    tree: ast.Module, rel: str, source_lines: list[str]
) -> list[_Hit]:
    """Flag every numeric default-arg in every function in *tree*."""
    hits: list[_Hit] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is None:
                continue
            hit = _classify_default(default, node, rel, source_lines)
            if hit is not None:
                hits.append(hit)
    return hits


def _hit_is_suppressed(hit: _Hit, source_lines: list[str]) -> bool:
    """Return True iff *hit*'s source line carries the suppression marker."""
    line_idx = hit.lineno - 1
    if not 0 <= line_idx < len(source_lines):
        return False
    return _line_has_trailing_marker(source_lines[line_idx])


def _classify_module_assign(
    name: str,
    value_node: ast.expr,
    annotation: ast.expr | None,
    rel: str,
    source_lines: list[str],
) -> _Hit | None:
    """Module-level ``NAME = <number>`` -> hit if not allowlisted.

    *annotation* is the PEP-526 annotation for ``ast.AnnAssign`` or
    ``None`` for bare ``ast.Assign``. A numeric named-constant marker
    short-circuits to ``None``; bare assignments still flag.
    """
    if _annotation_marks_as_named_constant(annotation):
        return None
    inner, negated = _unwrap_unary(value_node)
    if not _is_numeric_constant(inner):
        return None
    if _is_hex_literal(inner, source_lines):
        return None
    value = _effective_value(value_node)
    if value is None or value in _TRIVIAL_VALUES:
        return None
    raw = cast("int | float", inner.value)
    return _Hit(
        rel=rel,
        lineno=value_node.lineno,
        col=value_node.col_offset,
        kind=f"module-level-constant `{name}`",
        value=_format_value(raw, negated=negated),
    )


def _classify_default(  # noqa: PLR0911
    default: ast.expr,
    function_node: ast.FunctionDef | ast.AsyncFunctionDef,
    rel: str,
    source_lines: list[str],
) -> _Hit | None:
    """Default arg ``def f(p=<number>)`` -> hit if not allowlisted."""
    inner, negated = _unwrap_unary(default)
    if not isinstance(inner, ast.Constant):
        return None
    if _is_hex_literal(inner, source_lines):
        return None
    raw = inner.value
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = -raw if negated else raw
    if value in _TRIVIAL_VALUES:
        return None
    if _is_status_code_default(default, function_node):
        return None
    if _is_io_default(default, function_node, value):
        return None
    return _Hit(
        rel=rel,
        lineno=default.lineno,
        col=default.col_offset,
        kind=f"default-arg in `{function_node.name}`",
        value=_format_value(raw, negated=negated),
    )


def _format_value(raw: int | float, *, negated: bool = False) -> str:
    """Render *raw* preserving its original type (int vs float).

    A literal source spelling of ``30.0`` should render as ``30.0``,
    not the int form ``30``, even though the values compare equal.
    Callers pre-filter :class:`bool` (an ``int`` subclass) before
    reaching this helper.
    """
    if isinstance(raw, int):
        return str(-raw if negated else raw)
    if negated:
        return repr(-raw)
    return repr(raw)


# ── Baseline ────────────────────────────────────────────────────

_BASELINE_HEADER: Final[str] = """\
# Frozen baseline of pre-existing magic-number sites in src/synthorg/.
# Each line is `path:lineno:col` (POSIX path, 1-indexed line, 0-indexed
# column) sorted in deterministic order.
#
# scripts/check_no_magic_numbers.py reads this file to suppress
# violations at these exact locations. New sites NOT in this list will
# fail the pre-push hook. The baseline shrinks monotonically; PRs that
# migrate a literal to settings/definitions/ remove its line.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_no_magic_numbers.py --update
"""

_BASELINE_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"^.+:\d+:\d+$")


def _load_baseline(path: Path | None = None) -> set[str]:
    """Return the set of allowlisted ``path:lineno:col`` entries.

    Raises :class:`ValueError` on validation failures (malformed or
    duplicate entries), corrupted UTF-8 encoding, or unreadable file
    permissions. Failing loud here keeps the gate's promise -- a
    silently-truncated baseline would let real violations slip
    past the pre-push hook.
    """
    if path is None:
        path = _BASELINE_PATH
    if not path.exists():
        return set()
    entries: set[str] = set()
    errors: list[str] = []
    rel_path = (
        path.relative_to(_REPO_ROOT).as_posix()
        if (path.is_relative_to(_REPO_ROOT))
        else str(path)
    )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{rel_path}: cannot read baseline ({type(exc).__name__}: {exc})"
        raise ValueError(msg) from exc
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _BASELINE_ENTRY_RE.match(stripped):
            errors.append(
                f"{rel_path}:{lineno}: malformed entry "
                f"(expected 'path:lineno:col', got {stripped!r})"
            )
            continue
        if stripped in entries:
            errors.append(f"{rel_path}:{lineno}: duplicate entry {stripped!r}")
            continue
        entries.add(stripped)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        msg = (
            f"{rel_path}: baseline failed validation "
            f"({len(errors)} error{'s' if len(errors) != 1 else ''}); "
            f"regenerate with 'uv run python scripts/check_no_magic_numbers.py "
            f"--update' or fix by hand."
        )
        raise ValueError(msg)
    return entries


def _baseline_sort_key(entry: str) -> tuple[str, int, int]:
    """Tuple sort key (path, lineno, col) so numeric components order numerically.

    A pure string sort places ``foo.py:623:27`` before ``foo.py:73:30``
    because lexicographic comparison sees ``"6" < "7"``. Splitting the
    POSIX path off and casting line / column to ``int`` keeps the
    documented "path then numeric line then numeric column" invariant
    that the file header advertises.
    """
    path, lineno, col = entry.rsplit(":", 2)
    return (path, int(lineno), int(col))


def _write_baseline(hits: list[_Hit], path: Path | None = None) -> None:
    """Sort + write *hits* as a baseline file."""
    if path is None:
        path = _BASELINE_PATH
    keys = sorted({hit.baseline_key() for hit in hits}, key=_baseline_sort_key)
    body = _BASELINE_HEADER + "\n".join(keys) + "\n"
    path.write_text(body, encoding="utf-8")


# ── CLI ─────────────────────────────────────────────────────────


def _iter_targets(
    roots: list[Path],
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Yield ``(absolute_path, posix_relative_path)`` for every file to scan."""
    targets: list[tuple[Path, str]] = []
    for root in roots:
        abs_root = _resolve_root(root, project_root)
        if abs_root is None or not abs_root.exists():
            continue
        for path, rel in _git_tracked_python_files(abs_root, project_root):
            if _is_file_allowlisted(rel):
                continue
            targets.append((path, rel))
    return targets


def _scan_all(
    roots: list[Path],
    project_root: Path,
) -> list[_Hit]:
    hits: list[_Hit] = []
    for path, rel in _iter_targets(roots, project_root):
        hits.extend(_scan_file(path, rel))
    return hits


def cmd_update(roots: list[Path], project_root: Path) -> int:
    """Regenerate ``no_magic_numbers_baseline.txt`` from the current tree."""
    try:
        hits = _scan_all(roots, project_root)
    except ScanError as exc:
        print(f"check_no_magic_numbers: {exc}", file=sys.stderr)
        return 2
    baseline_path = _baseline_path(project_root)
    _write_baseline(hits, baseline_path)
    rel = baseline_path.relative_to(project_root).as_posix()
    print(
        f"Wrote {len({h.baseline_key() for h in hits})} entries to {rel}.",
        file=sys.stderr,
    )
    return 0


def cmd_scan(roots: list[Path], project_root: Path) -> int:
    """Scan and exit non-zero on any new violation outside the baseline."""
    try:
        baseline = _load_baseline(_baseline_path(project_root))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        hits = _scan_all(roots, project_root)
    except ScanError as exc:
        print(f"check_no_magic_numbers: {exc}", file=sys.stderr)
        return 2
    new_violations = [h for h in hits if h.baseline_key() not in baseline]
    if not new_violations:
        return 0
    new_violations.sort(key=lambda h: (h.rel, h.lineno, h.col))
    for hit in new_violations:
        print(hit.message())
    print(
        f"\n{len(new_violations)} new magic-number site(s) detected outside "
        "the baseline. Migrate the value to "
        "src/synthorg/settings/definitions/<namespace>.py and read it via "
        "ConfigResolver, or add "
        "'# lint-allow: magic-numbers -- <reason>' on the offending line.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="+",
        default=["src/synthorg"],
        help="Roots to scan (relative to repo root).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Project root to anchor path resolution against.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Regenerate scripts/no_magic_numbers_baseline.txt.",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    roots = [Path(p) for p in args.paths]
    for root in roots:
        if _resolve_root(root, project_root) is None:
            print(
                f"refusing to scan path outside project root: {root}",
                file=sys.stderr,
            )
            return 2

    if args.update:
        return cmd_update(roots, project_root)
    return cmd_scan(roots, project_root)


if __name__ == "__main__":
    sys.exit(main())
