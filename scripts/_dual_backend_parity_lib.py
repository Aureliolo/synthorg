"""Shared helpers for ``check_dual_backend_test_parity.py``.

Extracted into a sibling module so the entry-point script stays under
the CLAUDE.md 800-line ceiling.  Behaviour-preserving: every public
symbol below is consumed by ``check_dual_backend_test_parity`` (or
its unit-test suite) and nothing else.

Two violation kinds:

- :class:`_TestViolation` for signature- and body-pass findings on
  test functions under ``tests/conformance/persistence/``.
- :class:`_CoverageViolation` for repository protocol classes that
  are exposed on ``PersistenceBackend`` but never exercised in the
  conformance suite.

Three passes (orchestrated by the entry-point script):

1. Signature -- :func:`_collect_signature_violations` (via
   :func:`_check_signature`).
2. Body -- folded into the same walk via
   :func:`_check_body_backend_name_conditional`.
3. Coverage -- :func:`_discover_repo_classes`,
   :func:`_discover_backend_accessors`,
   :func:`_collect_backend_accessor_usage`,
   :func:`_build_coverage_violations`.

Per-line opt-out marker (``# lint-allow: dual-backend-parity --
<reason>``) is validated by :func:`_line_has_trailing_marker`.
Baseline I/O lives here too (:func:`_load_baseline`,
:func:`_write_baseline`, :func:`_apply_baseline`).
"""

import ast
import io
import re
import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

__all__ = (
    "_BASELINE_HEADER",
    "_CoverageViolation",
    "_TestViolation",
    "_apply_baseline",
    "_build_coverage_violations",
    "_check_body_backend_name_conditional",
    "_check_signature",
    "_collect_backend_accessor_usage",
    "_collect_body_violations",
    "_collect_coverage_violations",
    "_collect_signature_violations",
    "_discover_backend_accessors",
    "_discover_repo_classes",
    "_function_signature_lines",
    "_iter_test_functions",
    "_line_has_trailing_marker",
    "_load_baseline",
    "_read_and_parse",
    "_scan_signature_file",
    "_write_baseline",
)

# ── Constants ───────────────────────────────────────────────────

_SUPPRESSION_MARKER: Final[str] = "lint-allow: dual-backend-parity"
_REQUIRED_PARAM_NAME: Final[str] = "backend"
_BACKEND_NAME_ATTR: Final[str] = "backend_name"

# Bare class names unique to the persistence layer; any direct
# annotation by these names bypasses the conftest fixture.
_FORBIDDEN_BARE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "SQLitePersistenceBackend",
        "PostgresPersistenceBackend",
        "SQLiteConfig",
        "PostgresConfig",
    }
)

# (module, attr) pairs forbidden when the annotation is dotted.  Bare
# ``Connection`` is too generic; only the dotted forms are flagged so
# unrelated ``Connection`` types from other libraries pass cleanly.
_FORBIDDEN_DOTTED_NAMES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("aiosqlite", "Connection"),
        ("psycopg", "AsyncConnection"),
        ("psycopg", "Connection"),
        ("psycopg_pool", "AsyncConnectionPool"),
    }
)

# Repository protocol class names: ``UserRepository``, ``PresetOverrideRepo``,
# ``ProviderAuditRepo``.  PascalCase-only excludes private helpers; the
# explicit ``Repos?(?:itory)?$`` filters out helper / data classes
# (``IdempotencyOutcome``, ``IdempotencyClaim``) sharing the file.
_REPO_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Z][A-Za-z0-9]*Repos?(?:itory)?$"
)

ViolationKind = Literal[
    "missing-backend-param",
    "direct-backend-typing",
    "backend-name-conditional",
]

# Validates each non-comment baseline line; raises on typos rather
# than letting them silently rot as permanent stale entries.  Mirrors
# the parity-with-the-loader contract in
# ``check_setting_to_startup_trace``.
_BASELINE_ENTRY_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:"
    r"missing-test-coverage:[A-Za-z0-9_]+"
    r"|(?:missing-backend-param|direct-backend-typing|backend-name-conditional):"
    r"[^:]+:\d+:[A-Za-z0-9_]+(?::[A-Za-z0-9_]+)?"
    r")$"
)

_BASELINE_HEADER: Final[str] = (
    "# Baseline of pre-existing dual-backend test parity violations.\n"
    "# Each line is one of:\n"
    "#   missing-test-coverage:<RepositoryClassName>\n"
    "#   missing-backend-param:<rel_posix_path>:<lineno>:<func_name>\n"
    "#   direct-backend-typing:<rel_posix_path>:<lineno>:<func_name>:<param_name>\n"
    "#   backend-name-conditional:<rel_posix_path>:<lineno>:<func_name>\n"
    "# (direct-backend-typing carries the offending parameter name so\n"
    "#  multiple forbidden annotations on one function each baseline\n"
    "#  separately.)\n"
    "# Regenerate with --update-baseline (commit diff after review).\n"
)


# ── Models ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _TestViolation:
    """Per-test signature- or body-pass violation.

    *subject* names the offending element when one violation kind can
    fire multiple times against the same function -- specifically
    ``direct-backend-typing``, where each forbidden parameter
    annotation produces its own entry.  Without it, two forbidden
    params on one ``def`` would collapse to one baseline key and one
    of them would silently slip past reporting and ratcheting.
    ``None`` keeps the legacy key shape for kinds that fire at most
    once per function.
    """

    kind: ViolationKind
    rel_path: str
    lineno: int
    func_name: str
    detail: str
    subject: str | None = None

    def baseline_key(self) -> str:
        """Return ``<kind>:<rel_path>:<lineno>:<func_name>[:<subject>]``."""
        suffix = f":{self.subject}" if self.subject is not None else ""
        return f"{self.kind}:{self.rel_path}:{self.lineno}:{self.func_name}{suffix}"

    def message(self) -> str:
        """Return the stderr violation line."""
        return (
            f"{self.rel_path}:{self.lineno}: {self.kind} "
            f"({self.func_name}) -- {self.detail}"
        )


@dataclass(frozen=True)
class _CoverageViolation:
    """Repository whose ``backend.<accessor>`` is never used in tests.

    *accessors* lists every accessor on ``PersistenceBackend`` whose
    return type resolves to ``repo_class``.  Generic protocols
    (``VersionRepository[T]``) bind to N attributes -- ``workflow_versions``,
    ``identity_versions``, etc. -- and any one of them counts as
    coverage.  Storing the full tuple keeps the violation message
    accurate when none of the accessors are exercised.
    """

    repo_class: str
    accessors: tuple[str, ...]

    def baseline_key(self) -> str:
        """Return ``missing-test-coverage:<repo_class>``."""
        return f"missing-test-coverage:{self.repo_class}"

    def message(self) -> str:
        """Return the stderr violation line."""
        if len(self.accessors) == 1:
            target = f"backend.{self.accessors[0]}"
        else:
            target = "any of " + ", ".join(f"backend.{a}" for a in self.accessors)
        return (
            f"missing-test-coverage: no test under tests/conformance/persistence/"
            f" exercises {target} (returns {self.repo_class}). "
            "Add a Test class that consumes the parametrised `backend` fixture "
            "and calls into one of these accessors at least once."
        )


# ── Suppression marker (mirrors check_persistence_boundary) ─────


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries a valid suppression marker.

    Required form: ``# lint-allow: dual-backend-parity -- <reason>``
    with non-empty reason after ``--``.
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
        if suffix.startswith("--") and suffix[2:].strip():
            return True
    return False


# ── Annotation classification ───────────────────────────────────


def _annotation_terminal_names(
    node: ast.AST | None,
) -> Iterable[tuple[str | None, str]]:
    """Yield ``(module, terminal)`` pairs for every name in *node*.

    Unwraps ``ast.Subscript`` (``Optional[X]``, ``AsyncIterator[X]``)
    and ``ast.BinOp`` (``X | None``) so the gate inspects every name
    in a composite annotation.

    - ``Name(id='X')``         -> ``(None, 'X')``
    - ``Attribute(value=Name('m'), attr='X')`` -> ``('m', 'X')``
    - ``Constant(value='X')``  -> parse as ``ast.parse(value, mode="eval")``
      and recurse so quoted dotted forward refs like
      ``"psycopg.AsyncConnection"`` reach the dotted-form filter.
    - ``Call(func=...)``       -> recurse into ``func`` and ``args``
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
        try:
            parsed = ast.parse(node.value, mode="eval")
        except SyntaxError:
            yield None, node.value
        else:
            yield from _annotation_terminal_names(parsed.body)
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
    if isinstance(node, ast.Call):
        yield from _annotation_terminal_names(node.func)
        for arg in node.args:
            yield from _annotation_terminal_names(arg)
        return


def _forbidden_terminal(annotation: ast.AST | None) -> str | None:
    """Return the first forbidden terminal name in *annotation*, else None."""
    for module, terminal in _annotation_terminal_names(annotation):
        if module is None and terminal in _FORBIDDEN_BARE_NAMES:
            return terminal
        if module is not None and (module, terminal) in _FORBIDDEN_DOTTED_NAMES:
            return f"{module}.{terminal}"
    return None


# ── Test-function discovery ─────────────────────────────────────


def _iter_test_functions(
    tree: ast.AST,
) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield every ``def test_*`` / ``async def test_*`` node in *tree*."""
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            yield node


def _function_signature_lines(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[int]:
    """Return 1-indexed line numbers spanning *func*'s signature.

    Includes the decorator(s), ``def``/``async def`` line, every
    signature-continuation line through the closing ``)``, and the
    return-annotation line.  Uses ``end_lineno`` (Python 3.8+) for the
    precise upper bound and walks decorators (whose ``lineno`` precedes
    ``func.lineno``) so an author-placed marker on a decorator or any
    signature-continuation line is honoured wherever it sits.
    """
    start = func.lineno
    for decorator in func.decorator_list:
        start = min(start, decorator.lineno)
    body_start = func.body[0].lineno if func.body else func.end_lineno or func.lineno
    end = (body_start - 1) if body_start > func.lineno else func.lineno
    if func.returns is not None:
        end = max(end, func.returns.lineno)
    return set(range(start, end + 1))


# ── Pass 1 + 2: signature and body violations per test function ──


def _check_signature(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    rel_path: str,
) -> list[_TestViolation]:
    """Return signature-pass violations for one test function."""
    violations: list[_TestViolation] = []
    positional = [*func.args.posonlyargs, *func.args.args]
    args = [a for a in positional if a.arg not in {"self", "cls"}]
    args.extend(func.args.kwonlyargs)
    if func.args.vararg is not None:
        args.append(func.args.vararg)
    if func.args.kwarg is not None:
        args.append(func.args.kwarg)
    if not any(a.arg == _REQUIRED_PARAM_NAME for a in args):
        violations.append(
            _TestViolation(
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
                _TestViolation(
                    kind="direct-backend-typing",
                    rel_path=rel_path,
                    lineno=func.lineno,
                    func_name=func.name,
                    detail=(
                        f"parameter `{arg.arg}` is annotated as `{forbidden}`, "
                        "which bypasses the parametrised conformance fixture; "
                        "use `backend: PersistenceBackend` instead"
                    ),
                    subject=arg.arg,
                )
            )
    return violations


def _is_backend_name_attr(node: ast.AST) -> bool:
    """``True`` for ``backend.backend_name`` (Attribute on Name 'backend')."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == _BACKEND_NAME_ATTR
        and isinstance(node.value, ast.Name)
        and node.value.id == _REQUIRED_PARAM_NAME
    )


def _check_body_backend_name_conditional(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    rel_path: str,
) -> list[_TestViolation]:
    """Flag ``backend.backend_name == "X"`` style comparisons in *func*'s body.

    Catches both the ``ast.Compare`` form (``if backend.backend_name == "x"``)
    and the ``ast.Match`` form (``match backend.backend_name:``), since both
    silently turn a test into a one-arm skip (only sqlite OR only postgres
    exercises the code path), which the parametrisation seam is designed to
    prevent. Legitimate exceptions (e.g. backend-specific feature tests)
    carry the suppression marker on the test signature.
    """
    violations: list[_TestViolation] = []
    detail = (
        "test body branches on `backend.backend_name`, which silently turns "
        "dual-backend conformance into a one-arm test; either remove the "
        "conditional, split the test into two, or add the suppression marker "
        "on the signature with a rationale"
    )
    for stmt in func.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                if not any(_is_backend_name_attr(op) for op in operands):
                    continue
            elif isinstance(node, ast.Match):
                if not _is_backend_name_attr(node.subject):
                    continue
            else:
                continue
            violations.append(
                _TestViolation(
                    kind="backend-name-conditional",
                    rel_path=rel_path,
                    lineno=node.lineno,
                    func_name=func.name,
                    detail=detail,
                )
            )
            break
    return violations


def _read_and_parse(file_path: Path, rel_path: str) -> tuple[str, ast.Module]:
    """Read + parse *file_path*; raise ``ValueError`` on failure.

    Used by every gate pass.  Failing loud (rather than silently
    skipping) ensures a corrupted test or protocol file surfaces at CI
    time instead of letting violations slip past undetected.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{rel_path}: unable to read file: {exc}"
        raise ValueError(msg) from exc
    try:
        tree = ast.parse(text, filename=str(file_path))
    except SyntaxError as exc:
        msg = f"{rel_path}:{exc.lineno or 0}: unable to parse file: {exc.msg}"
        raise ValueError(msg) from exc
    return text, tree


def _collect_signature_violations(
    file_path: Path,
    rel_path: str,
) -> list[_TestViolation]:
    """Return signature + body violations for one conformance test file.

    Suppression marker on any signature line silences both signature-pass
    and body-pass checks for that test.
    """
    text, tree = _read_and_parse(file_path, rel_path)
    lines = text.splitlines()
    out: list[_TestViolation] = []
    for func in _iter_test_functions(tree):
        signature_lines = [
            lines[ln - 1]
            for ln in _function_signature_lines(func)
            if 1 <= ln <= len(lines)
        ]
        if any(_line_has_trailing_marker(line) for line in signature_lines):
            continue
        out.extend(_check_signature(func, rel_path))
        out.extend(_check_body_backend_name_conditional(func, rel_path))
    return out


def _collect_body_violations(
    file_path: Path,
    rel_path: str,
) -> list[_TestViolation]:
    """Return only the body-pass violations for one file.

    Exposed for direct testing; production code path uses
    :func:`_collect_signature_violations` which folds both passes
    into a single AST walk.
    """
    _, tree = _read_and_parse(file_path, rel_path)
    out: list[_TestViolation] = []
    for func in _iter_test_functions(tree):
        out.extend(_check_body_backend_name_conditional(func, rel_path))
    return out


def _scan_signature_file(file_path: Path, rel_path: str) -> list[str]:
    """Return formatted signature + body violation messages for one file.

    Test-facing wrapper around :func:`_collect_signature_violations`.
    """
    return [v.message() for v in _collect_signature_violations(file_path, rel_path)]


# ── Pass 3: protocol coverage ───────────────────────────────────


def _class_has_protocol_base(cls: ast.ClassDef) -> bool:
    """Return True iff *cls* lists ``Protocol`` as a base (any depth)."""
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


_PROTOCOL_DIR_SKIP: Final[frozenset[str]] = frozenset({"__init__.py", "conftest.py"})


def _discover_repo_classes(protocol_dir: Path) -> set[str]:
    """Walk ``*.py`` files in *protocol_dir*; return repo class names.

    Two collection paths:

    - ``class XxxRepository(Protocol):`` / ``class XxxRepo(Protocol):``
      defined directly in the file.
    - Re-exports via ``from X import YRepository`` (with or without
      ``as Y``) where the name matches the repo regex; covers
      ``escalation_protocol.py`` which re-exports from another subsystem.

    Scans every top-level ``.py`` (skipping ``__init__.py`` /
    ``conftest.py``) so protocols defined in ``*_repo.py`` /
    ``*_repository.py`` (e.g. ``version_repo.py``,
    ``preset_repository.py``, ``training_repos.py``) are covered the
    same way as ``*_protocol.py`` files.  ``Path.glob`` is non-recursive,
    so concrete ``Sqlite*`` / ``Postgres*`` classes living under the
    backend subdirectories never enter this scan.

    Filters strictly by name regex (:data:`_REPO_NAME_RE`).  Read /
    parse failures raise ``ValueError`` -- a corrupt protocol file
    that gets silently skipped would let the gate miss a coverage gap.
    """
    found: set[str] = set()
    for path in sorted(protocol_dir.glob("*.py")):
        if path.name in _PROTOCOL_DIR_SKIP:
            continue
        rel = f"src/synthorg/persistence/{path.name}"
        _, tree = _read_and_parse(path, rel)
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


def _extract_return_type_name(
    node: ast.AST | None,
) -> str | None:
    """Return the bare name of a return-type annotation, or None.

    Accepts ``Name``, ``Attribute`` (uses ``attr``), and string forward
    references (``Constant(value="X")``).  Strips a single layer of
    ``Subscript`` / ``BinOp`` to handle generics and unions; for a
    ``BinOp`` (``X | None``), prefers a side that matches the repo
    regex so ``UserRepository | None`` resolves to ``UserRepository``.
    Falls back to the left side when neither matches.
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


def _discover_backend_accessors(backend_protocol_path: Path) -> dict[str, list[str]]:
    """Return ``{repo_class_name: [accessor_name, ...]}`` from ``PersistenceBackend``.

    Reads the ``PersistenceBackend`` class from
    ``src/synthorg/persistence/protocol.py``.  Each method whose return
    annotation matches the repo regex contributes one entry:

    - ``@property`` methods (``users -> UserRepository``).
    - Plain methods (``build_escalations -> EscalationQueueRepository``).

    Generic protocols (``VersionRepository[T]``) typically bind to
    several accessors (``workflow_versions``, ``identity_versions``,
    ``role_versions``, ...); each accessor is appended in declaration
    order so the coverage pass treats any one of them as evidence the
    protocol is exercised.  Read / parse failure raises ``ValueError``
    -- the protocol file is the central registry; silently returning an
    empty map would disable the entire coverage pass.
    """
    rel = "src/synthorg/persistence/protocol.py"
    _, tree = _read_and_parse(backend_protocol_path, rel)
    accessor_for: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "PersistenceBackend":
            continue
        for member in node.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            return_name = _extract_return_type_name(member.returns)
            if return_name is None or not _REPO_NAME_RE.match(return_name):
                continue
            accessor_for.setdefault(return_name, []).append(member.name)
    return accessor_for


def _collect_backend_accessor_usage(conformance_dir: Path) -> set[str]:
    """Return every direct ``backend.<attr>`` accessor name used in tests.

    Walks every ``.py`` file under *conformance_dir* (excluding
    ``conftest.py`` and ``__init__.py``) and records the *attr* of
    every AST ``Attribute`` whose ``value`` is exactly
    ``Name(id="backend")``.  Multi-level chains like
    ``backend.users.api_keys.save(x)`` register only the first hop
    (``users``); the inner ``Attribute(value=Attribute(...), attr="api_keys")``
    has a non-Name value and is ignored.  Read / parse failures raise
    so a broken test file surfaces at CI time.

    Skipping fixture / package files keeps the coverage check honest:
    a ``backend.<accessor>`` reference inside ``conftest.py`` exists
    only to construct the fixture itself, not to exercise the
    repository.  Counting it would let a fixture-only mention satisfy
    coverage when no real conformance test ever touches that repo.
    """
    used: set[str] = set()
    if not conformance_dir.is_dir():
        return used
    for path in sorted(conformance_dir.rglob("*.py")):
        if path.name in _PROTOCOL_DIR_SKIP:
            continue
        try:
            rel = path.relative_to(conformance_dir.parent.parent.parent).as_posix()
        except ValueError:
            rel = path.name
        _, tree = _read_and_parse(path, rel)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == _REQUIRED_PARAM_NAME
            ):
                used.add(node.attr)
    return used


def _collect_coverage_violations(
    repo_classes: set[str],
    accessor_for: dict[str, list[str]],
    used_accessors: set[str],
) -> list[str]:
    """Return formatted coverage-violation messages."""
    return [
        v.message()
        for v in _build_coverage_violations(repo_classes, accessor_for, used_accessors)
    ]


def _build_coverage_violations(
    repo_classes: set[str],
    accessor_for: dict[str, list[str]],
    used_accessors: set[str],
) -> list[_CoverageViolation]:
    """Return structured coverage violations (test-facing helper).

    A repo class is covered when *any* of its accessors appears in
    ``used_accessors``.  Generic protocols bound to multiple attributes
    on ``PersistenceBackend`` therefore pass coverage as soon as one
    accessor is exercised.
    """
    out: list[_CoverageViolation] = []
    for repo_class in sorted(repo_classes):
        accessors = accessor_for.get(repo_class)
        if not accessors:
            continue
        if not any(accessor in used_accessors for accessor in accessors):
            out.append(
                _CoverageViolation(
                    repo_class=repo_class,
                    accessors=tuple(accessors),
                )
            )
    return out


# ── Baseline I/O ────────────────────────────────────────────────


def _load_baseline(path: Path) -> set[str]:
    """Parse *path* into the entry-key set; raise on malformed lines.

    Missing file returns an empty set (baseline is optional).  Each
    non-blank non-comment line must match :data:`_BASELINE_ENTRY_RE`;
    a typo silently rotting in the baseline is exactly the failure
    mode the gate exists to prevent.
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
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _BASELINE_ENTRY_RE.match(stripped):
            msg = (
                f"{path.as_posix()}:{lineno}: malformed baseline entry "
                f"(expected one of <kind>:<...>, got {stripped!r})"
            )
            raise ValueError(msg)
        entries.add(stripped)
    return entries


def _write_baseline(path: Path, entries: set[str]) -> None:
    """Overwrite *path* with the canonical header + sorted entries."""
    body = _BASELINE_HEADER + "\n".join(sorted(entries)) + ("\n" if entries else "")
    path.write_text(body, encoding="utf-8")


def _apply_baseline(
    current: set[str], baseline: set[str]
) -> tuple[list[str], list[str]]:
    """Return ``(new, stale)``: violations not in baseline; baseline gone."""
    return sorted(current - baseline), sorted(baseline - current)
