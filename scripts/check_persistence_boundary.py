"""Pre-push / CI persistence-boundary gate.

Enforces the rule: ``src/synthorg/persistence/`` is the only place that
may import ``aiosqlite``, ``sqlite3``, ``psycopg``, or ``psycopg_pool``,
or emit raw SQL DDL/DML keywords in string literals.  Every durable
feature must go through a repository Protocol in ``persistence/``.

Sanctioned exceptions (agent tools that legitimately need raw DB
introspection) are listed in ``_ALLOWLIST`` below.  To opt out on a
single line, add ``# lint-allow: persistence-boundary -- <reason>``
as a trailing comment on that line.  The justification after ``--`` is
required and must be non-empty.

Exits non-zero with a structured list on violations.

Usage:
    python scripts/check_persistence_boundary.py
    python scripts/check_persistence_boundary.py --paths src/synthorg
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
from typing import Final

# ── Patterns ────────────────────────────────────────────────────

# Driver imports: forbidden outside the persistence/ boundary.
_DRIVER_IMPORT_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?:
        ^\s*import\s+(?P<mod>aiosqlite|sqlite3|psycopg|psycopg_pool)\b
        |
        ^\s*from\s+(?P<frm>aiosqlite|sqlite3|psycopg|psycopg_pool)[\.\s]
        |
        ^\s*from\s+(?P<frm2>aiosqlite|sqlite3|psycopg|psycopg_pool)\b
    )""",
    re.VERBOSE,
)

# Raw SQL DDL/DML keywords inside a string literal.  The trailing
# whitespace guard keeps "CREATE TABLE" / "INSERT INTO" from matching
# "CREATE_TABLE_PATTERN" or "INSERTION_COUNT".  Heuristic -- a handful
# of false positives are expected and can be silenced with the marker.
_RAW_SQL_RE: Final[re.Pattern[str]] = re.compile(
    r"""['"]                                   # opening quote
        [^'"]*?                                # any preamble
        \b(?:
            CREATE\s+TABLE
            |INSERT\s+INTO
            |ALTER\s+TABLE
            |DROP\s+TABLE
            |UPDATE\s+\w+\s+SET
            |DELETE\s+FROM
        )\b
        [^'"]*                                 # any trailing SQL
        ['"]""",
    re.VERBOSE | re.IGNORECASE,
)

# Mutation audit log calls inside the persistence boundary.  Repos must
# not log mutations themselves -- the service layer is the audit point.
# Per-line opt-out via the existing
# ``# lint-allow: persistence-boundary -- <reason>`` marker.
#
# Detection lives in :func:`_scan_persistence_mutation_logs` and uses
# the AST so it catches the full surface: ``logger.info``,
# ``self._logger.warning``, renamed loggers, *every* logging level, and
# events passed by keyword.  Comments and docstrings are ignored by
# construction (they are not ``Call`` nodes).

# Logging methods whose first positional / ``event`` keyword argument is
# inspected for a mutation-suffix constant.  ``exception`` / ``critical``
# are included so the gate cannot be bypassed by widening the level.
_LOGGING_METHODS: Final[frozenset[str]] = frozenset(
    {"debug", "info", "warning", "error", "exception", "critical"}
)

# Suffixes that mark an entity-mutation audit constant.  The first
# tuple covers SCREAMING_SNAKE constant names (``PERSISTENCE_USER_SAVED``);
# the second covers the corresponding event-string values
# (``"persistence.user.saved"``) so the scanner catches both
# constant references AND raw string literals at logger call sites.
_MUTATION_SUFFIXES: Final[tuple[str, ...]] = (
    "_SAVED",
    "_CREATED",
    "_UPDATED",
    "_DELETED",
    "_PERSISTED",
)
_MUTATION_VALUE_SUFFIXES: Final[tuple[str, ...]] = (
    ".saved",
    ".created",
    ".updated",
    ".deleted",
    ".persisted",
)

# Lifecycle / infrastructure events whose constants happen to end in a
# mutation suffix but are NOT entity-mutation audits (the rule targets
# entity audit, not "the backend started" / "TimescaleDB hypertable was
# initialised" lifecycle).  Each entry must carry a justification.
_MUTATION_LOG_ALLOWED_CONSTANTS: Final[frozenset[str]] = frozenset(
    {
        # Backend factory: lifecycle event when the persistence backend
        # itself is constructed; not an entity mutation.
        "PERSISTENCE_BACKEND_CREATED",
        # TimescaleDB hypertable conversion: schema-evolution lifecycle,
        # fired once per migration (not per row mutation).
        "PERSISTENCE_TIMESCALEDB_HYPERTABLE_CREATED",
        # Filesystem artifact storage: blob-store deletion event from
        # the storage abstraction (separate concern from the artifact
        # repository which already has its own audit at the service
        # layer via API_ARTIFACT_DELETED).
        "PERSISTENCE_ARTIFACT_STORAGE_DELETED",
    }
)

# ── Allowlist ───────────────────────────────────────────────────

# Files outside ``persistence/`` that are sanctioned exceptions.
# Only add a file here after a deliberate decision to carry SQL
# outside the repository boundary (e.g. agent tools that introspect
# arbitrary DB schemas at runtime).  Every entry should have a brief
# comment explaining *why* the exception is justified.
_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        # Agent-facing DB introspection tool.  Returns table/column
        # metadata from whatever DB the operator configured -- the
        # repository abstraction does not expose this shape by design.
        "src/synthorg/tools/database/schema_inspect.py",
        # Agent-facing arbitrary-SQL tool.  Operator-gated; runs
        # user-supplied SELECTs against the configured DB.  Cannot
        # ride the repository pattern because SQL strings are the
        # payload.
        "src/synthorg/tools/database/sql_query.py",
        # Destructive-operation detector scans user-supplied SQL for
        # DDL keywords -- the DDL literals are the *payload* of the
        # security check, not SQL emitted by the app.
        "src/synthorg/security/rules/destructive_op_detector.py",
        # The boundary checker itself references driver names in
        # patterns and error messages.
        "scripts/check_persistence_boundary.py",
        # Schema-drift revision gate: applies the per-backend yoyo
        # revisions to a throwaway DB and dumps the schema to compare
        # against the canonical ``schema.sql``. It legitimately holds a
        # driver handle and issues DDL for that bootstrap, same rationale
        # as the test-DB helpers below; it is a CI/dev gate, never app code.
        "scripts/check_schema_drift_revisions.py",
        # Shared conftest bootstraps the test database via aiosqlite.
        "tests/conftest.py",
        # Shared helper that builds a migrated Postgres template database
        # and clones per-test DBs from it; holds a psycopg driver and
        # issues CREATE/DROP/ALTER DATABASE DDL for that purpose -- same
        # test-DB-bootstrap rationale as tests/conftest.py.
        "tests/_shared/postgres_template.py",
        # Integration / unit tests that legitimately hold driver
        # handles for cross-subsystem fixtures.
        "tests/integration/engine/identity/test_identity_versioning.py",
        "tests/integration/engine/workflow/test_subworkflows_e2e.py",
        "tests/integration/hr/training/test_training_persistence.py",
        "tests/unit/hr/test_persistence.py",
        "tests/unit/memory/embedding/test_fine_tune_orchestrator.py",
        "tests/unit/meta/test_approval_repo.py",
        "tests/unit/ontology/drift/test_store.py",
        "tests/unit/tools/database/test_sql_query.py",
        "tests/unit/tools/database/test_schema_inspect.py",
        # Destructive-op-detector tests feed SQL fragments as test
        # inputs to the detector -- same reason as the detector itself.
        "tests/unit/security/rules/test_destructive_op_detector.py",
        # Boundary-checker self-test: feeds SQL keyword fixtures into
        # the matcher to prove it flags them.  The strings ARE the test
        # input -- adding suppression markers per line would defeat the
        # exact behavior under test.
        "tests/unit/scripts/test_check_persistence_boundary.py",
        # Schema-drift gate self-test: each scenario feeds synthetic
        # ``CREATE TABLE`` / ``CREATE INDEX`` strings to ``parse_schema``
        # to verify the gate's diff behaviour.  The strings ARE the test
        # input; per-line suppression markers would dwarf the test
        # assertions.
        "tests/unit/scripts/test_check_schema_drift.py",
    }
)

# Any path starting with one of these prefixes is considered inside
# the boundary and not subject to the rule.
_PERSISTENCE_PREFIXES: Final[tuple[str, ...]] = (
    "src/synthorg/persistence/",
    "tests/conformance/persistence/",
    # Per-backend unit tests drive repositories directly and
    # legitimately build aiosqlite / psycopg fixtures.
    "tests/unit/persistence/",
    "tests/integration/persistence/",
    # Auth-store unit tests instantiate repositories with an
    # aiosqlite connection fixture -- same shape as above.
    "tests/unit/api/auth/",
    # Backup handler tests exercise sqlite3 file-level copy/restore.
    "tests/unit/backup/test_handlers/",
)

_SUPPRESSION_MARKER: Final[str] = "lint-allow: persistence-boundary"


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the marker as a trailing ``#`` comment.

    The marker must be followed by ``--`` and non-empty justification
    text -- ``# lint-allow: persistence-boundary -- legacy fixture``.
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
        # Require "-- <non-empty>" after the marker prefix.
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--"):
            justification = suffix[2:].strip()
            if justification:
                return True
    return False


def _is_inside_boundary(rel: str) -> bool:
    """Return True iff *rel* lives inside the persistence boundary."""
    return any(rel.startswith(prefix) for prefix in _PERSISTENCE_PREFIXES)


def _scan_file(file_path: Path, rel: str) -> list[str]:
    """Return violation messages for a single file."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}:0: unable to scan file: {exc}"]
    issues: list[str] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if _line_has_trailing_marker(line):
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Driver imports -- only match on logical import lines.
        for match in _DRIVER_IMPORT_RE.finditer(line):
            mod = match.group("mod") or match.group("frm") or match.group("frm2")
            if mod:
                issues.append(
                    f"{rel}:{idx}: imports '{mod}' outside the persistence "
                    "boundary; wrap the call in a repository under "
                    "src/synthorg/persistence/ (or add "
                    "'# lint-allow: persistence-boundary -- <reason>' if the "
                    "exception is genuinely sanctioned)."
                )
        # Raw SQL DDL/DML keywords in string literals.
        if _RAW_SQL_RE.search(line):
            issues.append(
                f"{rel}:{idx}: raw SQL DDL/DML keyword in a string literal "
                "outside persistence/; move it into a repository module or "
                "add '# lint-allow: persistence-boundary -- <reason>'."
            )
    return issues


def _is_logger_call(call: ast.Call) -> str | None:
    """Return the logging level if *call* is a logger call, else ``None``.

    Recognises any attribute chain ending in a logging level
    (``logger.info``, ``self._logger.warning``, ``cls.log.error``, ...)
    so the gate cannot be bypassed by renaming the logger or hiding it
    behind ``self`` / ``cls``.
    """
    if not isinstance(call.func, ast.Attribute):
        return None
    if call.func.attr not in _LOGGING_METHODS:
        return None
    return call.func.attr


def _extract_event_node(call: ast.Call) -> ast.expr | None:
    """Return the AST node carrying the event-constant argument for *call*.

    Logging calls in this codebase pass the event constant as the first
    positional argument (``logger.info(EVENT, key=...)``); some sites
    pass it via the ``event`` keyword.  Both forms are accepted, and
    the returned node may be:

    - :class:`ast.Name` (``EVENT``); the canonical case.
    - :class:`ast.Attribute` (``events.EVENT`` / ``module.EVENT``);
      module-attribute access.
    - :class:`ast.Constant` with a string value
      (``logger.info("persistence.user.saved", ...)``); the
      escape-hatch literal form.

    Dynamic expressions (variables built at runtime, f-strings, etc.)
    are ignored: the audit-event protocol mandates static event names,
    so anything else is not a real audit emission worth gating.
    """
    if call.args:
        first = call.args[0]
        if isinstance(first, (ast.Name, ast.Attribute, ast.Constant)):
            return first
    for kw in call.keywords:
        if kw.arg == "event" and isinstance(
            kw.value, (ast.Name, ast.Attribute, ast.Constant)
        ):
            return kw.value
    return None


def _build_alias_map(tree: ast.AST) -> dict[str, str]:
    """Return ``{local_name: imported_name}`` for ``import ... as ...`` lines.

    Used to resolve aliased imports so the scanner cannot be bypassed
    by ``from synthorg.observability.events.persistence.user import
    PERSISTENCE_USER_SAVED as EVT`` followed by ``logger.info(EVT, ...)``.

    Module-level only -- function-scoped imports are ignored on the
    grounds that they are rare and adding scope tracking would balloon
    the script.  If a real escape ever shows up, the fallback string-
    value check below still catches it.
    """
    aliases: dict[str, str] = {}
    for node in tree.body if isinstance(tree, ast.Module) else ():
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
    return aliases


def _build_assignment_map(tree: ast.AST) -> dict[str, ast.expr]:
    """Return ``{local_name: rhs_node}`` for module-level assignments.

    Closes the second escape hatch: a repo could ``AUDIT_EVENT =
    PERSISTENCE_PROJECT_SAVED`` (or the literal string form) and then
    ``logger.info(AUDIT_EVENT, ...)``.  The aliased-import resolver
    only catches ``import ... as ...``; this resolver catches
    plain-`Assign` and `AnnAssign` redirections.

    The map values are the right-hand-side AST expressions, which the
    caller dereferences via :func:`_resolve_event_token` (one level
    of indirection -- chains of more than one redirection are rare
    enough that the extra complexity is not worth it).
    """
    assignments: dict[str, ast.expr] = {}
    for node in tree.body if isinstance(tree, ast.Module) else ():
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assignments[node.target.id] = node.value
    return assignments


def _resolve_event_token(
    node: ast.expr,
    aliases: dict[str, str],
    assignments: dict[str, ast.expr],
) -> tuple[str | None, str | None]:
    """Resolve *node* to ``(canonical_name, literal_value)``.

    ``canonical_name`` is the identifier form
    (``PERSISTENCE_USER_SAVED``) when the event reference is a
    :class:`ast.Name`/:class:`ast.Attribute`; ``literal_value`` is the
    string form (``"persistence.user.saved"``) when the event is
    written as a :class:`ast.Constant`.  Either may be ``None``.

    Resolution order for :class:`ast.Name`:

    1. ``aliases`` -- ``import X as Y`` rewrites Y back to X.
    2. ``assignments`` -- ``Y = X`` (or ``Y = "..."``) follows one
       level of redirection so module-level reassignment cannot
       bypass the suffix check.
    3. Fall back to the local name itself -- the suffix check still
       fires when an unresolved Name happens to end in a mutation
       suffix on its own.
    """
    if isinstance(node, ast.Name):
        if node.id in aliases:
            return aliases[node.id], None
        if node.id in assignments:
            rhs = assignments[node.id]
            if isinstance(rhs, ast.Name):
                return aliases.get(rhs.id, rhs.id), None
            if isinstance(rhs, ast.Attribute):
                return rhs.attr, None
            if isinstance(rhs, ast.Constant) and isinstance(rhs.value, str):
                return None, rhs.value
        return node.id, None
    if isinstance(node, ast.Attribute):
        # ``events.PERSISTENCE_FOO`` -> the rightmost attribute is the
        # constant name we care about.  Module-prefix is irrelevant
        # for the suffix check.
        return node.attr, None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return None, node.value
    return None, None


def _has_mutation_suffix(constant: str) -> bool:
    """``True`` when *constant* (identifier form) ends in a mutation suffix."""
    return any(constant.endswith(suffix) for suffix in _MUTATION_SUFFIXES)


def _has_mutation_value_suffix(value: str) -> bool:
    """``True`` when *value* (string-literal form) ends in a mutation suffix."""
    lower = value.lower()
    return any(lower.endswith(suffix) for suffix in _MUTATION_VALUE_SUFFIXES)


def _scan_persistence_mutation_logs(file_path: Path, rel: str) -> list[str]:
    """Return mutation-log violations for a persistence-boundary file.

    Repos must not log mutations themselves; service-layer events are
    the canonical audit point.  This scanner walks the file's AST and
    finds every logging call whose event-constant argument ends in
    ``_SAVED``/``_CREATED``/``_UPDATED``/``_DELETED``/``_PERSISTED``,
    skipping (a) sanctioned lifecycle constants on
    ``_MUTATION_LOG_ALLOWED_CONSTANTS`` and (b) any line that carries
    the ``# lint-allow: persistence-boundary -- <reason>`` marker.

    Detection covers ``logger.<level>(EVENT, ...)``,
    ``self._logger.<level>(EVENT, ...)``, renamed-attribute loggers,
    every standard logging level (debug/info/warning/error/exception/
    critical), and the ``event=`` keyword form -- all of which the
    previous regex-based scanner would silently miss.  Comments and
    docstrings are ignored by construction (they are not ``Call``
    nodes).
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}:0: unable to scan file: {exc}"]
    try:
        tree = ast.parse(text, filename=str(file_path))
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno or 0}: unable to parse file: {exc.msg}"]
    issues: list[str] = []
    lines = text.splitlines()
    aliases = _build_alias_map(tree)
    assignments = _build_assignment_map(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_logger_call(node) is None:
            continue
        event_node = _extract_event_node(node)
        if event_node is None:
            continue
        canonical_name, literal_value = _resolve_event_token(
            event_node, aliases, assignments
        )
        # The event matches if either the canonical identifier OR the
        # literal string ends in a mutation suffix.  Resolved aliases
        # and ``module.CONST`` attribute references both surface as
        # ``canonical_name``; raw string literals surface as
        # ``literal_value``.
        is_mutation = (
            canonical_name is not None and _has_mutation_suffix(canonical_name)
        ) or (literal_value is not None and _has_mutation_value_suffix(literal_value))
        if not is_mutation:
            continue
        # Allowlist applies to the canonical identifier form only;
        # the lifecycle constants are always referenced by name in
        # this codebase.  String-literal usage is treated as a
        # deliberate escape hatch and never silently allowed.
        if (
            canonical_name is not None
            and canonical_name in _MUTATION_LOG_ALLOWED_CONSTANTS
        ):
            continue
        # Allow per-line opt-out on either the ``logger.<level>(`` line
        # OR the line carrying the event token -- multi-line calls
        # span both, and the marker can be placed on whichever read
        # more naturally.
        call_line_no = node.lineno
        event_line_no = event_node.lineno
        if _line_has_trailing_marker(
            lines[call_line_no - 1]
        ) or _line_has_trailing_marker(lines[event_line_no - 1]):
            continue
        display = canonical_name if canonical_name is not None else repr(literal_value)
        issues.append(
            f"{rel}:{call_line_no}: repo-level mutation audit log "
            f"{display} must move to the service layer "
            "(repositories must not log mutations themselves; see "
            "docs/reference/persistence-boundary.md). Add "
            "'# lint-allow: persistence-boundary -- <reason>' on the "
            "logger call line ONLY when the event is a non-entity "
            "lifecycle/infrastructure signal."
        )
    return issues


def _resolve_root(root: Path, project_root: Path) -> Path | None:
    """Resolve *root* to an absolute path strictly under *project_root*.

    Uses :func:`os.path.commonpath` (rather than :meth:`Path.relative_to`)
    as the containment check so CodeQL's path-injection data-flow
    analysis recognises the sanitizer.
    """
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
    """Return every tracked ``*.py`` file under *abs_root* as ``(abs, rel)``."""
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", f"{rel_root}/*.py"],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p]
    return [((project_root / rel_path), rel_path) for rel_path in paths]


def _iter_targets(
    roots: list[Path],
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Yield ``(absolute_path, posix_relative_path)`` for every file to scan.

    Files inside the persistence boundary and on the allowlist are
    excluded up front -- they are never violations.
    """
    targets: list[tuple[Path, str]] = []
    for root in roots:
        abs_root = _resolve_root(root, project_root)
        if abs_root is None or not abs_root.exists():
            continue
        for path, rel in _git_tracked_python_files(abs_root, project_root):
            if _is_inside_boundary(rel):
                continue
            if rel in _ALLOWLIST:
                continue
            # Tests other than persistence conformance fall under the
            # rule so a stray driver import in a unit test still trips
            # the gate.  Explicit exceptions belong in _ALLOWLIST.
            targets.append((path, rel))
    return targets


_PERSISTENCE_SRC_PREFIX: Final[str] = "src/synthorg/persistence/"


def _iter_persistence_targets(
    roots: list[Path],
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Yield ``(absolute_path, posix_relative_path)`` for persistence files.

    The mutation-log gate only ever needs to scan
    ``src/synthorg/persistence/``; user-supplied ``--paths`` are used as
    a *coarse opt-in* (skip persistence scanning entirely if no path
    overlaps the persistence tree) but do **not** drive the actual
    enumeration -- that is anchored at a hard-coded prefix under
    ``project_root``.  Avoiding user-input on the final filesystem read
    keeps CodeQL's path-injection analysis happy and prevents the
    persistence sweep from accidentally widening scope when callers
    pass aggressive ``--paths``.

    Tests under ``tests/.../persistence/`` are intentionally excluded:
    the rule applies to production code; tests that exercise repo
    internals can log whatever they need.
    """
    if not _persistence_in_scope(roots, project_root):
        return []
    persistence_root = project_root / _PERSISTENCE_SRC_PREFIX.rstrip("/")
    if not persistence_root.is_dir():
        return []
    targets: list[tuple[Path, str]] = []
    for path, rel in _git_tracked_python_files(persistence_root, project_root):
        if rel.startswith(_PERSISTENCE_SRC_PREFIX):
            targets.append((path, rel))
    return targets


def _persistence_in_scope(roots: list[Path], project_root: Path) -> bool:
    """Return ``True`` when at least one ``--paths`` entry overlaps persistence.

    Coarse opt-in for the mutation-log sweep -- uses the same
    containment check as :func:`_resolve_root` so the boundary is
    consistent.  The function never returns the user-supplied path
    itself, only a boolean derived from it.
    """
    persistence_prefix = (project_root / _PERSISTENCE_SRC_PREFIX.rstrip("/")).resolve(
        strict=False
    )
    for root in roots:
        resolved = _resolve_root(root, project_root)
        if resolved is None:
            continue
        # Either the user path is inside persistence, or persistence is
        # inside the user path -- both count as "persistence is in
        # scope".  Compare resolved string forms so CodeQL recognises
        # the prefix relationship.
        resolved_str = os.fspath(resolved)
        prefix_str = os.fspath(persistence_prefix)
        if (
            resolved_str == prefix_str
            or resolved_str.startswith(prefix_str + os.sep)
            or prefix_str.startswith(resolved_str + os.sep)
        ):
            return True
    return False


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory.

    Carries the diagnostic message intended for stderr so :func:`main`
    can format it consistently and exit with a non-zero status.
    """


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Args:
        repo_root: User-supplied repo root from ``--repo-root``, or
            ``None`` to fall back to the script's own parent directory.

    Returns:
        A resolved :class:`Path` to the project root.

    Raises:
        ProjectRootError: If ``--repo-root`` is inaccessible (OSError on
            resolve) or does not point at a directory.  The message
            attached to the exception is suitable for printing directly
            to stderr.
    """
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


def _scan_all(
    roots: list[Path],
    project_root: Path,
) -> int:
    """Run both scanning passes, print issues, return total count."""
    total = 0
    for path, rel in _iter_targets(roots, project_root):
        issues = _scan_file(path, rel)
        for msg in issues:
            print(msg)
        total += len(issues)
    # Mutation-log gate: scan persistence-boundary files for repo-level
    # mutation audit logs.  These violate the service-layer rule
    # (repositories must not log mutations themselves).
    for path, rel in _iter_persistence_targets(roots, project_root):
        issues = _scan_persistence_mutation_logs(path, rel)
        for msg in issues:
            print(msg)
        total += len(issues)
    return total


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="+",
        default=["src/synthorg", "tests"],
        help="Roots to scan (relative to repo root).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Project root to anchor path resolution against.  Defaults to "
            "the ancestor directory of this script; pass "
            "${{ github.workspace }} in CI to remove all ambiguity."
        ),
    )
    args = parser.parse_args()

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

    total = _scan_all(roots, project_root)
    if total:
        print(
            f"\n{total} persistence-boundary violation(s) found.  See "
            "docs/guides/persistence-migrations.md for the repository "
            "pattern and the opt-out marker format.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
