"""Pre-push / CI gate: dual-backend test parity for persistence conformance.

Two passes:

1. **Signature** -- every ``def test_*`` / ``async def test_*`` under
   ``tests/conformance/persistence/`` (and outside ``conftest.py``)
   must accept a ``backend`` parameter, and no parameter may be
   annotated with a concrete driver / backend type that bypasses the
   parametrisation seam (``aiosqlite.Connection``,
   ``psycopg.AsyncConnection``, ``psycopg.Connection``,
   ``psycopg_pool.AsyncConnectionPool``,
   ``SQLitePersistenceBackend``, ``PostgresPersistenceBackend``,
   ``SQLiteConfig``, ``PostgresConfig``).
2. **Coverage** -- every repository protocol class defined or
   re-exported under ``src/synthorg/persistence/*_protocol.py`` that
   is exposed on ``PersistenceBackend`` (via ``@property`` or method)
   must be exercised by at least one ``backend.<accessor>`` access in
   the conformance suite.

The shared ``backend`` fixture in
``tests/conformance/persistence/conftest.py`` is parametrised over
``["sqlite", "postgres"]``, so any test consuming it automatically
runs against both backends. The signature check protects that seam;
the coverage check ensures every repository sits on it.

Per-line opt-out (signature only): append
``# lint-allow: dual-backend-parity -- <reason>`` to any line of the
test signature. The justification after ``--`` is required and must be
non-empty (mirrors ``# lint-allow: persistence-boundary``).

Baseline file ``scripts/dual_backend_parity_baseline.txt`` freezes
pre-existing violations so the gate can ship without forcing fixes in
the same PR. New violations fail; stale baseline entries warn (but
pass) so the file can be tightened over time. Regenerate via
``--update-baseline`` (commit the diff after manual review).

Usage::

    python scripts/check_dual_backend_test_parity.py
    python scripts/check_dual_backend_test_parity.py --repo-root /path/to/repo
    python scripts/check_dual_backend_test_parity.py --update-baseline
"""

import argparse
import ast
import io
import re
import sys
import tokenize
from collections.abc import Iterable  # noqa: TC003 -- runtime use in generators
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Names below are accessed dynamically by the unit tests via
# ``importlib`` (``_MODULE._scan_signature_file`` etc.). Listing them
# here documents the test-facing surface and silences "not accessed"
# hints from static analyzers that cannot see the dynamic lookup.
__all__ = (
    "ProjectRootError",
    "_apply_baseline",
    "_collect_backend_accessor_usage",
    "_collect_coverage_violations",
    "_discover_backend_accessors",
    "_discover_repo_classes",
    "_load_baseline",
    "_scan_signature_file",
    "_write_baseline",
    "main",
)

# ── Constants ───────────────────────────────────────────────────

_SUPPRESSION_MARKER: Final[str] = "lint-allow: dual-backend-parity"

_REQUIRED_PARAM_NAME: Final[str] = "backend"

# Bare class names that uniquely belong to the persistence layer; any
# direct annotation by these names bypasses the conftest fixture.
_FORBIDDEN_BARE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "SQLitePersistenceBackend",
        "PostgresPersistenceBackend",
        "SQLiteConfig",
        "PostgresConfig",
    }
)

# (module, attr) pairs forbidden when the annotation is dotted. Bare
# ``Connection`` is not on this list because it is too generic; only
# the dotted forms are flagged so unrelated ``Connection`` types from
# other libraries do not produce false positives.
_FORBIDDEN_DOTTED_NAMES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("aiosqlite", "Connection"),
        ("psycopg", "AsyncConnection"),
        ("psycopg", "Connection"),
        ("psycopg_pool", "AsyncConnectionPool"),
    }
)

# Match repository protocol class names: ``UserRepository``,
# ``PresetOverrideRepo``, ``ProviderAuditRepo`` -- but NOT helper /
# data classes like ``IdempotencyOutcome`` or ``IdempotencyClaim``.
_REPO_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Z][A-Za-z0-9]*Repos?(?:itory)?$"
)

_BASELINE_HEADER: Final[str] = (
    "# Frozen baseline of pre-existing dual-backend test parity violations.\n"
    "# Each line is one of:\n"
    "#   missing-test-coverage:<RepositoryClassName>\n"
    "#   missing-backend-param:<rel_posix_path>:<lineno>:<func_name>\n"
    "#   direct-backend-typing:<rel_posix_path>:<lineno>:<func_name>\n"
    "#\n"
    "# scripts/check_dual_backend_test_parity.py reads this file to\n"
    "# suppress violations at these exact entries. New violations NOT\n"
    "# in this list fail the pre-push hook; stale entries warn (but\n"
    "# pass) so the file can be tightened over time.\n"
    "#\n"
    "# Regenerate (rare; requires explicit user approval) with:\n"
    "#   uv run python scripts/check_dual_backend_test_parity.py "
    "--update-baseline\n"
)


# ── Models ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _SignatureViolation:
    """One signature-pass violation.

    Attributes:
        kind: ``"missing-backend-param"`` or ``"direct-backend-typing"``.
        rel_path: Relative POSIX path to the test file.
        lineno: 1-indexed line of the offending ``def``.
        func_name: Name of the offending test function / method.
        detail: Short human message printed in the violation line.
    """

    kind: str
    rel_path: str
    lineno: int
    func_name: str
    detail: str

    def baseline_key(self) -> str:
        """Return ``<kind>:<rel_path>:<lineno>:<func_name>`` (baseline form)."""
        return f"{self.kind}:{self.rel_path}:{self.lineno}:{self.func_name}"

    def message(self) -> str:
        """Return the stderr violation line."""
        return f"{self.rel_path}:{self.lineno}: {self.kind} ({self.func_name}) -- {self.detail}"


@dataclass(frozen=True)
class _CoverageViolation:
    """One coverage-pass violation.

    Attributes:
        repo_class: Repository protocol class name (e.g. ``UserRepository``).
        accessor: PersistenceBackend property / method name that returns
            this repo (e.g. ``users``).
    """

    repo_class: str
    accessor: str

    def baseline_key(self) -> str:
        """Return ``missing-test-coverage:<repo_class>``."""
        return f"missing-test-coverage:{self.repo_class}"

    def message(self) -> str:
        """Return the stderr violation line."""
        return (
            f"missing-test-coverage: no test under tests/conformance/persistence/ "
            f"exercises backend.{self.accessor} (returns {self.repo_class}). "
            "Add a Test class that consumes the parametrised `backend` fixture "
            "and calls into this accessor at least once."
        )


# ── Suppression marker (mirrors check_persistence_boundary) ─────


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries a valid suppression marker.

    Required form::

        # lint-allow: dual-backend-parity -- <non-empty justification>
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


# ── Annotation classification ───────────────────────────────────


def _annotation_terminal_names(  # noqa: PLR0911 -- one branch per AST node shape
    node: ast.AST | None,
) -> Iterable[tuple[str | None, str]]:
    """Yield ``(module, terminal)`` pairs for every name in *node*.

    Unwraps ``ast.Subscript`` (``Optional[X]``, ``AsyncIterator[X]``)
    and ``ast.BinOp`` (``X | None``) so the gate inspects every name
    in a composite annotation.

    - ``Name(id='X')``         -> ``(None, 'X')``
    - ``Attribute(value=Name('m'), attr='X')`` -> ``('m', 'X')``
    - ``Constant(value='X')``  -> ``(None, 'X')`` (string forward ref)
    """
    if node is None:
        return
    if isinstance(node, ast.Name):
        yield None, node.id
        return
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        yield node.value.id, node.attr
        return
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        # Forward reference like ``"UserRepository"``; emit as bare.
        yield None, node.value
        return
    if isinstance(node, ast.Subscript):
        yield from _annotation_terminal_names(node.value)
        yield from _annotation_terminal_names(node.slice)
        return
    if isinstance(node, ast.Tuple):
        for elt in node.elts:
            yield from _annotation_terminal_names(elt)
        return
    if isinstance(node, ast.BinOp):
        yield from _annotation_terminal_names(node.left)
        yield from _annotation_terminal_names(node.right)
        return


def _forbidden_terminal(annotation: ast.AST | None) -> str | None:
    """Return the first forbidden terminal name in *annotation*, else None."""
    for module, terminal in _annotation_terminal_names(annotation):
        if module is None and terminal in _FORBIDDEN_BARE_NAMES:
            return terminal
        if module is not None and (module, terminal) in _FORBIDDEN_DOTTED_NAMES:
            return f"{module}.{terminal}"
    return None


# ── Signature pass ──────────────────────────────────────────────


def _iter_test_functions(
    tree: ast.AST,
) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield every ``def test_*`` / ``async def test_*`` node in *tree*."""
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            yield node


def _function_signature_lines(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """Return the 1-indexed line numbers spanning *func*'s signature.

    Spans the ``def``/``async def`` line through the last argument's
    line (or the ``def`` line if there are no arguments) so the
    suppression marker is honoured wherever the author placed it on
    the signature.
    """
    start = func.lineno
    end = func.lineno
    args = list(func.args.args) + list(func.args.kwonlyargs)
    for arg in args:
        end = max(end, arg.lineno)
    if func.args.vararg is not None:
        end = max(end, func.args.vararg.lineno)
    if func.args.kwarg is not None:
        end = max(end, func.args.kwarg.lineno)
    if func.returns is not None:
        end = max(end, func.returns.lineno)
    return set(range(start, end + 1))


def _scan_signature_file(file_path: Path, rel_path: str) -> list[str]:
    """Return formatted signature-violation messages for one file.

    Public-ish (underscore-prefixed) so the unit-test suite can drive
    it directly with synthetic source files.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel_path}:0: unable to scan file: {exc}"]
    try:
        tree = ast.parse(text, filename=str(file_path))
    except SyntaxError as exc:
        return [f"{rel_path}:{exc.lineno or 0}: unable to parse file: {exc.msg}"]
    lines = text.splitlines()
    violations: list[_SignatureViolation] = []
    for node in _iter_test_functions(tree):
        signature_line_nos = _function_signature_lines(node)
        signature_lines = [
            lines[ln - 1] for ln in signature_line_nos if 1 <= ln <= len(lines)
        ]
        suppressed = any(_line_has_trailing_marker(line) for line in signature_lines)
        violations.extend(_check_function(node, rel_path, suppressed=suppressed))
    return [v.message() for v in violations]


def _check_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    rel_path: str,
    *,
    suppressed: bool,
) -> list[_SignatureViolation]:
    """Return signature violations for one test function.

    Two checks:

    1. Must have a parameter named ``backend`` (excluding ``self`` /
       ``cls``). Missing -> ``missing-backend-param``.
    2. No parameter may be annotated as a forbidden type. Each
       forbidden annotation -> ``direct-backend-typing``.

    Suppression silences both checks. The marker is gate-wide rather
    than per-violation; legitimate exceptions (e.g. a deliberately
    sqlite-only constraint test) get a single comment that explains
    the deviation.
    """
    if suppressed:
        return []
    violations: list[_SignatureViolation] = []
    args = [a for a in func.args.args if a.arg not in {"self", "cls"}]
    args.extend(func.args.kwonlyargs)
    if func.args.vararg is not None:
        args.append(func.args.vararg)
    if func.args.kwarg is not None:
        args.append(func.args.kwarg)
    has_backend_param = any(a.arg == _REQUIRED_PARAM_NAME for a in args)
    if not has_backend_param:
        violations.append(
            _SignatureViolation(
                kind="missing-backend-param",
                rel_path=rel_path,
                lineno=func.lineno,
                func_name=func.name,
                detail=(
                    "test function does not accept the parametrised `backend: "
                    "PersistenceBackend` fixture, so it cannot run against "
                    "both SQLite and Postgres"
                ),
            )
        )
    for arg in args:
        forbidden = _forbidden_terminal(arg.annotation)
        if forbidden is not None:
            violations.append(
                _SignatureViolation(
                    kind="direct-backend-typing",
                    rel_path=rel_path,
                    lineno=func.lineno,
                    func_name=func.name,
                    detail=(
                        f"parameter `{arg.arg}` is annotated as `{forbidden}`, "
                        "which bypasses the parametrised conformance fixture; "
                        "use `backend: PersistenceBackend` instead"
                    ),
                )
            )
    return violations


# ── Coverage pass: protocol discovery ───────────────────────────


def _class_has_protocol_base(cls: ast.ClassDef) -> bool:
    """Return True iff *cls* lists ``Protocol`` (or a Subscript thereof) as a base."""
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Protocol":
            return True
        if isinstance(base, ast.Subscript):
            inner = base.value
            if isinstance(inner, ast.Name) and inner.id == "Protocol":
                return True
            if isinstance(inner, ast.Attribute) and inner.attr == "Protocol":
                return True
    return False


def _discover_repo_classes(protocol_dir: Path) -> set[str]:
    """Walk ``*_protocol.py`` files in *protocol_dir*; return repo class names.

    Two collection paths:

    1. ``class XxxRepository(Protocol):`` / ``class XxxRepo(Protocol):``
       defined directly in the file.
    2. Re-exports via ``from X import YRepository`` (with or without
       ``as Y``) where the name matches the repo regex; covers
       ``escalation_protocol.py`` which re-exports from another
       subsystem.

    Filters strictly by name regex (``_REPO_NAME_RE``) so non-repo
    classes (Pydantic models, enums) in the same file are skipped.
    """
    found: set[str] = set()
    for path in sorted(protocol_dir.glob("*_protocol.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if _REPO_NAME_RE.match(node.name) and _class_has_protocol_base(node):
                    found.add(node.name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if _REPO_NAME_RE.match(name):
                        found.add(name)
    return found


def _extract_return_type_name(  # noqa: PLR0911 -- one branch per AST node shape
    node: ast.AST | None,
) -> str | None:
    """Return the bare name of a return-type annotation, or None.

    Accepts ``Name``, ``Attribute`` (uses ``attr``), and string
    forward references (``Constant(value="X")``). Strips a single
    layer of ``Subscript`` / ``BinOp`` to handle ``UserRepository |
    None`` etc. Returns the first matching name; the coverage check
    only needs the repo class identifier.
    """
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Subscript):
        return _extract_return_type_name(node.value)
    if isinstance(node, ast.BinOp):
        for child in (node.left, node.right):
            name = _extract_return_type_name(child)
            if name is not None and _REPO_NAME_RE.match(name):
                return name
        return _extract_return_type_name(node.left)
    return None


def _discover_backend_accessors(backend_protocol_path: Path) -> dict[str, str]:
    """Return ``{repo_class_name: accessor_name}`` from ``PersistenceBackend``.

    Reads the ``PersistenceBackend`` class from
    ``src/synthorg/persistence/protocol.py``. Each method whose return
    annotation matches the repo regex contributes one entry:

    - ``@property`` methods (``users -> UserRepository``)
    - Plain methods (``build_escalations -> EscalationQueueRepository``)

    A repo type referenced more than once keeps the first accessor
    seen (top-of-class wins) -- the actual conformance signal is "any
    test exercises any accessor that returns this repo", so the
    specific accessor name is informational.
    """
    try:
        text = backend_protocol_path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return {}
    try:
        tree = ast.parse(text, filename=str(backend_protocol_path))
    except SyntaxError:
        return {}
    accessor_for: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "PersistenceBackend":
            continue
        for member in node.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            return_name = _extract_return_type_name(member.returns)
            if return_name is None or not _REPO_NAME_RE.match(return_name):
                continue
            accessor_for.setdefault(return_name, member.name)
    return accessor_for


# ── Coverage pass: backend.<accessor> usage detection ───────────


def _collect_backend_accessor_usage(conformance_dir: Path) -> set[str]:
    """Return every ``backend.<attr>`` accessor name used in conformance tests.

    Walks every ``test_*.py`` (and any other ``.py``) under
    *conformance_dir*; for each AST ``Attribute`` node whose ``value``
    resolves (terminally) to a ``Name(id="backend")``, records
    ``attr``. Multi-level chains like ``backend.users.save(x)`` produce
    ``users`` (the inner attribute). Dotted ``backend.users.api_keys``
    contributes both ``users`` and ``api_keys`` (the latter is also a
    repo accessor on the User repository, but the gate only treats the
    first hop off ``backend`` as evidence of coverage; nested chains
    are ignored to avoid false-positive coverage of unrelated repos).
    """
    used: set[str] = set()
    if not conformance_dir.is_dir():
        return used
    for path in sorted(conformance_dir.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if isinstance(node.value, ast.Name) and node.value.id == "backend":
                used.add(node.attr)
    return used


# ── Coverage pass: end-to-end ───────────────────────────────────


def _collect_coverage_violations(
    repo_classes: set[str],
    accessor_for: dict[str, str],
    used_accessors: set[str],
) -> list[str]:
    """Return formatted coverage-violation messages.

    Repo classes without a PersistenceBackend accessor are silently
    skipped (out of scope -- they are reachable some other way or
    deliberately not on the backend facade). For each repo with an
    accessor, the corresponding conformance suite must reference
    ``backend.<accessor>`` at least once.
    """
    violations: list[_CoverageViolation] = []
    for repo_class in sorted(repo_classes):
        accessor = accessor_for.get(repo_class)
        if accessor is None:
            continue
        if accessor not in used_accessors:
            violations.append(
                _CoverageViolation(repo_class=repo_class, accessor=accessor)
            )
    return [v.message() for v in violations]


# ── Baseline I/O ────────────────────────────────────────────────


def _load_baseline(path: Path) -> set[str]:
    """Parse *path* into the entry-key set, ignoring blank / comment lines.

    Missing file returns an empty set (not an error -- baseline is
    optional). Unparseable file raises ``ValueError`` so the caller
    can return exit code 2.
    """
    if not path.is_file():
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Cannot read baseline file {path.as_posix()}: {exc}"
        raise ValueError(msg) from exc
    except UnicodeDecodeError as exc:
        msg = f"Baseline file {path.as_posix()} has encoding error: {exc}"
        raise ValueError(msg) from exc
    entries: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped)
    return entries


def _write_baseline(path: Path, entries: set[str]) -> None:
    """Overwrite *path* with the canonical header + sorted entries."""
    body = _BASELINE_HEADER + "\n".join(sorted(entries)) + ("\n" if entries else "")
    path.write_text(body, encoding="utf-8")


def _apply_baseline(
    current: set[str], baseline: set[str]
) -> tuple[list[str], list[str]]:
    """Return ``(new, stale)``: violations not in baseline; baseline entries gone."""
    new = sorted(current - baseline)
    stale = sorted(baseline - current)
    return new, stale


# ── Top-level scan ──────────────────────────────────────────────


def _scan_signature_pass(conformance_dir: Path) -> list[tuple[str, str]]:
    """Return ``(baseline_key, message)`` for every signature violation.

    Iterates every ``.py`` under *conformance_dir* except ``conftest.py``
    (which legitimately constructs concrete backends) and ``__init__.py``.
    """
    if not conformance_dir.is_dir():
        return []
    repo_root = conformance_dir.parent.parent.parent
    out: list[tuple[str, str]] = []
    for path in sorted(conformance_dir.rglob("*.py")):
        if path.name in {"conftest.py", "__init__.py"}:
            continue
        try:
            rel_path = path.relative_to(repo_root).as_posix()
        except ValueError:
            rel_path = path.name
        # _scan_signature_file returns formatted messages directly;
        # re-parse here to recover the structured form needed for
        # baseline keys. Cheap: each file is tiny.
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out.append(
                (
                    f"unparseable:{rel_path}",
                    f"{rel_path}:0: unable to scan file: {exc}",
                )
            )
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            out.append(
                (
                    f"unparseable:{rel_path}",
                    f"{rel_path}:{exc.lineno or 0}: unable to parse file: {exc.msg}",
                )
            )
            continue
        lines = text.splitlines()
        for node in _iter_test_functions(tree):
            signature_line_nos = _function_signature_lines(node)
            signature_lines = [
                lines[ln - 1] for ln in signature_line_nos if 1 <= ln <= len(lines)
            ]
            suppressed = any(
                _line_has_trailing_marker(line) for line in signature_lines
            )
            out.extend(
                (v.baseline_key(), v.message())
                for v in _check_function(node, rel_path, suppressed=suppressed)
            )
    return out


def _scan_coverage_pass(
    persistence_dir: Path,
    conformance_dir: Path,
) -> list[tuple[str, str]]:
    """Return ``(baseline_key, message)`` for every coverage violation."""
    repo_classes = _discover_repo_classes(persistence_dir)
    accessor_for = _discover_backend_accessors(persistence_dir / "protocol.py")
    used_accessors = _collect_backend_accessor_usage(conformance_dir)
    out: list[tuple[str, str]] = []
    for repo_class in sorted(repo_classes):
        accessor = accessor_for.get(repo_class)
        if accessor is None:
            continue
        if accessor not in used_accessors:
            v = _CoverageViolation(repo_class=repo_class, accessor=accessor)
            out.append((v.baseline_key(), v.message()))
    return out


def _scan_repo(project_root: Path) -> list[tuple[str, str]]:
    """Run both passes; return ``[(baseline_key, message), ...]`` sorted."""
    persistence_dir = project_root / "src" / "synthorg" / "persistence"
    conformance_dir = project_root / "tests" / "conformance" / "persistence"
    out: list[tuple[str, str]] = []
    out.extend(_scan_signature_pass(conformance_dir))
    out.extend(_scan_coverage_pass(persistence_dir, conformance_dir))
    out.sort(key=lambda pair: pair[0])
    return out


# ── CLI ─────────────────────────────────────────────────────────


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments."""
    default_root = Path(__file__).resolve().parent.parent
    if repo_root is None:
        return default_root
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Project root to scan. Defaults to the script's repo. "
            "Pass ${{ github.workspace }} in CI to remove ambiguity."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Path to the baseline file. Defaults to "
            "scripts/dual_backend_parity_baseline.txt under the resolved "
            "repo root."
        ),
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Overwrite the baseline file with the current violation set "
            "(commit the diff after manual review)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    baseline_path = args.baseline or (
        project_root / "scripts" / "dual_backend_parity_baseline.txt"
    )

    pairs = _scan_repo(project_root)
    current_keys = {key for key, _ in pairs}
    message_for = dict(pairs)

    if args.update_baseline:
        try:
            _write_baseline(baseline_path, current_keys)
        except OSError as exc:
            print(
                f"Cannot write baseline {baseline_path.as_posix()}: {exc}",
                file=sys.stderr,
            )
            return 2
        print(
            f"Wrote {len(current_keys)} entries to {baseline_path.as_posix()}.",
            file=sys.stderr,
        )
        return 0

    try:
        baseline = _load_baseline(baseline_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    new, stale = _apply_baseline(current_keys, baseline)

    for key in new:
        print(message_for[key], file=sys.stderr)

    if stale:
        print(
            f"\nWarning: {len(stale)} stale baseline entries (no longer violated):",
            file=sys.stderr,
        )
        for entry in stale:
            print(f"  {entry}", file=sys.stderr)
        print(
            "Regenerate via 'uv run python scripts/check_dual_backend_test_parity.py "
            "--update-baseline' once the fix has merged.",
            file=sys.stderr,
        )

    if new:
        print(
            f"\n{len(new)} new dual-backend parity violation(s). Either fix the "
            "test signature (use `backend: PersistenceBackend`) / add a Test "
            "class that exercises the missing repo, or add the per-line opt-out "
            "'# lint-allow: dual-backend-parity -- <reason>' if the deviation "
            "is genuinely sanctioned.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
