"""Pre-push / CI persistence-backend property return-type uniformity gate.

For every ``@property`` declared on the ``PersistenceBackend`` Protocol
in ``src/synthorg/persistence/protocol.py``, both concrete backends
(``src/synthorg/persistence/{sqlite,postgres}/backend.py``) MUST
declare the matching ``@property`` with a return-type annotation that
resolves to the SAME identifier as the protocol declared. ``TaskRepository``
matches; ``SQLiteTaskRepository`` does not, even if it satisfies the
protocol structurally -- the public surface must hide the dialect choice
from callers.

The gate parses each file's AST, builds a map of
``{property_name: normalised_return_annotation}`` for the protocol class,
then walks each backend's class body and compares the matching property's
return annotation. Module-prefix (``persistence.task_protocol.TaskRepository``
vs ``TaskRepository``) is normalised away; generic parameters are
preserved (``VersionRepository[WorkflowDefinition]`` matches
``VersionRepository[WorkflowDefinition]`` but not
``PostgresVersionRepository[WorkflowDefinition]``).

Per-line opt-out: append ``# lint-allow: persistence-protocol-uniformity --
<required justification>`` as a trailing comment on the ``def <prop>``
line. The justification after ``--`` is required and must be non-empty.

Exits non-zero with a structured violation list. Designed to mirror the
shape of ``scripts/check_persistence_boundary.py``.

Usage:
    python scripts/check_persistence_protocol_return_types.py
    python scripts/check_persistence_protocol_return_types.py --repo-root <path>
"""

import argparse
import ast
import io
import sys
import tokenize
from pathlib import Path
from typing import Final

_PROTOCOL_PATH: Final[str] = "src/synthorg/persistence/protocol.py"
_PROTOCOL_CLASS_NAME: Final[str] = "PersistenceBackend"
# (path, expected concrete-class-name, accessor sidecars): the gate
# scans the named backend class plus any explicitly-listed accessor
# sidecars (mixin classes that contribute ``@property`` accessors to
# the backend's public surface). A future helper class with a
# colliding ``@property`` name still cannot silently overwrite the
# real backend's annotation because the sidecar list is declared
# here, not discovered.
_BACKEND_PATHS: Final[tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]] = (
    (
        "src/synthorg/persistence/sqlite/backend.py",
        "SQLitePersistenceBackend",
        (
            (
                "src/synthorg/persistence/sqlite/_backend_accessors.py",
                "_BackendRepositoryAccessors",
            ),
        ),
    ),
    (
        "src/synthorg/persistence/postgres/backend.py",
        "PostgresPersistenceBackend",
        (),
    ),
)
_SUPPRESSION_MARKER: Final[str] = "lint-allow: persistence-protocol-uniformity"


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


def _line_has_trailing_marker(line: str) -> bool:
    """Return True iff *line* carries the suppression marker as a trailing comment.

    Marker syntax: ``# lint-allow: persistence-protocol-uniformity -- <reason>``.
    The reason after ``--`` is required.
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


def _normalise_annotation(node: ast.expr | None) -> str:  # noqa: PLR0911
    """Return a normalised string form of *node*.

    Drops module prefixes from attribute chains
    (``persistence.task_protocol.TaskRepository`` becomes
    ``TaskRepository``) so the comparison sees the leaf identifier.
    Generic subscripts are preserved.
    """
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        base = _normalise_annotation(node.value)
        param = _normalise_annotation(node.slice)
        return f"{base}[{param}]"
    if isinstance(node, ast.Tuple):
        return ", ".join(_normalise_annotation(elt) for elt in node.elts)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


_PropertyNode = ast.FunctionDef | ast.AsyncFunctionDef


def _as_property(item: ast.AST) -> _PropertyNode | None:
    """Return *item* narrowed to a property-decorated function, else ``None``."""
    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    is_property = any(
        (isinstance(dec, ast.Name) and dec.id == "property")
        for dec in item.decorator_list
    )
    return item if is_property else None


def _collect_protocol_properties(tree: ast.Module) -> dict[str, str]:
    """Return ``{property_name: normalised_return_annotation}`` for the Protocol."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == _PROTOCOL_CLASS_NAME:
            collected: dict[str, str] = {}
            for item in node.body:
                prop = _as_property(item)
                if prop is None:
                    continue
                collected[prop.name] = _normalise_annotation(prop.returns)
            return collected
    return {}


def _collect_backend_properties(
    tree: ast.Module,
    backend_class_name: str,
) -> dict[str, tuple[str, int]]:
    """Return ``{property_name: (normalised_return, line_number)}``.

    Scans ONLY the class named *backend_class_name*; helper classes
    declared alongside the concrete backend never contribute to the
    surface checked against the protocol so a future name collision
    on a property cannot silently mask a real backend annotation.
    Returns an empty mapping when the named class is absent.
    """
    properties: dict[str, tuple[str, int]] = {}
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef) or cls.name != backend_class_name:
            continue
        for item in cls.body:
            prop = _as_property(item)
            if prop is None:
                continue
            properties[prop.name] = (
                _normalise_annotation(prop.returns),
                prop.lineno,
            )
        return properties
    return properties


def _load_class_properties(
    project_root: Path,
    rel: str,
    class_name: str,
) -> tuple[dict[str, tuple[str, int]], str | None]:
    """Return ``({property_name: (return, line)}, error)`` for one file+class.

    A missing file returns an empty mapping with no error so sidecar
    callers can treat a missing accessor module as "no contribution"
    (callers that actually require the file present check it
    separately before invoking this helper). Parse and read errors
    return an error message for the caller to surface.
    """
    path = project_root / rel
    if not path.is_file():
        return {}, None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"{rel}:0: unable to scan file: {exc}"
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return {}, f"{rel}:{exc.lineno or 0}: unable to parse file: {exc.msg}"
    return _collect_backend_properties(tree, class_name), None


def _merge_accessor_surface(
    project_root: Path,
    backend_rel: str,
    backend_props: dict[str, tuple[str, int]],
    accessor_sidecars: tuple[tuple[str, str], ...],
) -> tuple[dict[str, tuple[str, str, int]], list[str]]:
    """Merge sidecar accessor properties on top of backend properties.

    Returns ``(surface, sidecar_load_errors)`` where surface maps each
    property name to ``(source_rel, annotation, line_number)``. Backend
    properties win over any same-name sidecar entry.
    """
    surface: dict[str, tuple[str, str, int]] = {
        name: (backend_rel, ann, lineno)
        for name, (ann, lineno) in backend_props.items()
    }
    errors: list[str] = []
    for sidecar_rel, sidecar_class in accessor_sidecars:
        sidecar_props, sidecar_err = _load_class_properties(
            project_root, sidecar_rel, sidecar_class
        )
        if sidecar_err is not None:
            errors.append(sidecar_err)
            continue
        for name, (ann, lineno) in sidecar_props.items():
            if name in surface:
                continue
            surface[name] = (sidecar_rel, ann, lineno)
    return surface, errors


def _format_mismatch(
    source_rel: str,
    prop_name: str,
    actual: str,
    expected: str,
    lineno: int,
) -> str:
    """Format the property-return-type mismatch violation message."""
    return (
        f"{source_rel}:{lineno}: property {prop_name!r} returns {actual!r}; "
        f"PersistenceBackend protocol declares {expected!r}. The public "
        f"surface MUST hide the dialect choice from callers (see "
        f"docs/reference/persistence-boundary.md). Either flip the return "
        f"annotation to {expected!r} or add "
        f"'# lint-allow: persistence-protocol-uniformity -- <reason>' on "
        f"the property line."
    )


def _is_property_suppressed(project_root: Path, source_rel: str, lineno: int) -> bool:
    """Return True if the property declaration line carries the marker."""
    try:
        text = (project_root / source_rel).read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return False
    lines = text.splitlines()
    line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
    return _line_has_trailing_marker(line)


def _scan_backend(
    project_root: Path,
    rel: str,
    backend_class_name: str,
    accessor_sidecars: tuple[tuple[str, str], ...],
    protocol_props: dict[str, str],
) -> list[str]:
    """Return violation messages for one backend file + its accessor sidecars.

    Accessor sidecars are mixin classes that contribute ``@property``
    accessors to the backend's public surface. Their property
    declarations are merged into the backend's view before comparison
    against the protocol. Each property's source file is tracked so
    violation messages point at the file that actually declares the
    property.
    """
    backend_props, backend_err = _load_class_properties(
        project_root, rel, backend_class_name
    )
    if backend_err is not None:
        return [backend_err]
    if not backend_props:
        return [
            f"{rel}:0: concrete backend class {backend_class_name!r} not found "
            "in module; the gate has nothing to compare against."
        ]

    surface, issues = _merge_accessor_surface(
        project_root, rel, backend_props, accessor_sidecars
    )
    for prop_name, expected in protocol_props.items():
        if prop_name not in surface:
            issues.append(
                f"{rel}:0: property {prop_name!r} declared on PersistenceBackend "
                f"protocol is missing from this backend."
            )
            continue
        source_rel, actual, lineno = surface[prop_name]
        if actual == expected:
            continue
        if _is_property_suppressed(project_root, source_rel, lineno):
            continue
        issues.append(_format_mismatch(source_rel, prop_name, actual, expected, lineno))
    return issues


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Mirrors the helper in :mod:`scripts.check_persistence_boundary` so
    both gates raise the same diagnostic shape.
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


def _scan_all(project_root: Path) -> int:
    """Run the protocol vs backend comparison and print violations."""
    protocol_path = project_root / _PROTOCOL_PATH
    if not protocol_path.is_file():
        print(
            f"{_PROTOCOL_PATH}:0: protocol module not found",
            file=sys.stderr,
        )
        return 1
    try:
        protocol_text = protocol_path.read_text(encoding="utf-8")
        protocol_tree = ast.parse(protocol_text, filename=str(protocol_path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        print(
            f"{_PROTOCOL_PATH}:0: unable to parse protocol module: {exc}",
            file=sys.stderr,
        )
        return 1
    protocol_props = _collect_protocol_properties(protocol_tree)
    if not protocol_props:
        print(
            f"{_PROTOCOL_PATH}:0: no @property declarations found on "
            f"{_PROTOCOL_CLASS_NAME}; the gate has nothing to compare against.",
            file=sys.stderr,
        )
        return 1

    total = 0
    for rel, backend_class_name, accessor_sidecars in _BACKEND_PATHS:
        backend_path = project_root / rel
        if not backend_path.is_file():
            print(
                f"{rel}:0: backend module not found "
                "(expected concrete impl alongside the protocol).",
                file=sys.stderr,
            )
            total += 1
            continue
        violations = _scan_backend(
            project_root,
            rel,
            backend_class_name,
            accessor_sidecars,
            protocol_props,
        )
        for msg in violations:
            print(msg)
        total += len(violations)
    return total


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Project root anchor (defaults to this script's parent directory).",
    )
    args = parser.parse_args()

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    total = _scan_all(project_root)
    if total:
        print(
            f"\n{total} persistence-protocol-uniformity violation(s) found. "
            "See docs/reference/persistence-boundary.md and the property "
            "comparison rule above.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


# Public re-exports used by the unit tests (which import via importlib
# and reach for the private helpers directly so the harness exercises
# the same code paths the CLI runs at push time).
__all__ = [
    "ProjectRootError",
    "_collect_backend_properties",
    "_collect_protocol_properties",
    "_line_has_trailing_marker",
    "_normalise_annotation",
    "_resolve_project_root",
    "_scan_all",
    "_scan_backend",
    "main",
]
