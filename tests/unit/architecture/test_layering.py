"""Architecture-layering guard for the boundary-error abstraction.

Issue #1610 relocated the error taxonomy and the generic / persistence
exception hierarchies into ``synthorg.core``.  These tests pin the new
layering for the long term:

* No module under ``src/synthorg`` may import ``synthorg.api.errors``
  or ``synthorg.persistence.errors`` -- both modules are deleted; any
  reference is dead code that would fail at runtime anyway, but the
  static AST sweep catches it at lint speed and prevents the legacy
  paths from creeping back in via copy-paste.
* Lower layers (``engine/``, ``tools/``, ``budget/`` and friends) may
  not import from the ``synthorg.api.errors.*`` namespace at all -- the
  whole point of moving error classes to ``core`` was to lift the
  upward dependency.
* ``api/controllers/*.py`` must route persistence errors through
  ``synthorg.core.persistence_errors`` rather than reaching back into
  the persistence package internals.

The tests deliberately do not run via pytest collection's import phase
-- they parse files with ``ast`` so a stray ``from synthorg.api.errors``
in a non-imported module still fails the suite.
"""

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SRC = _REPO / "src" / "synthorg"

# Layers below the API boundary.  These directories must never import
# from the (deleted) ``synthorg.api.errors`` namespace -- the relocation
# to ``core`` was specifically to break the upward dependency.
_LOWER_LAYERS: frozenset[str] = frozenset(
    {
        "engine",
        "tools",
        "budget",
        "communication",
        "integrations",
        "ontology",
        "providers",
        "hr",
        "memory",
        "meta",
        "settings",
        "templates",
        "a2a",
        "backup",
        "observability",
        "core",
    }
)

# Module paths that must not appear in any ``from ... import ...`` block
# anywhere under ``src/synthorg``.  Both modules were deleted; any
# import is a regression.
_FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "synthorg.api.errors",
        "synthorg.persistence.errors",
    }
)


def _python_files(root: Path) -> list[Path]:
    """Return every ``.py`` file under ``root`` with ``__pycache__`` excluded."""
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _imported_modules(path: Path) -> set[str]:
    """Return every module referenced by an import statement.

    Walks both ``from MODULE import ...`` (``ast.ImportFrom``) and bare
    ``import MODULE`` / ``import MODULE as alias`` (``ast.Import``) at
    top level and inside function bodies, so a stray ``import
    synthorg.api.errors`` cannot slip past the layering guard.
    """
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:  # pragma: no cover - defensive
        msg = f"could not parse {path}: {exc}"
        raise AssertionError(msg) from exc
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
    return out


@pytest.mark.unit
def test_no_module_imports_legacy_error_paths() -> None:
    """No module under ``src/synthorg`` references the deleted error modules.

    ``synthorg.api.errors`` and ``synthorg.persistence.errors`` were
    removed in #1610.  This sweep catches any stray ``from
    synthorg.api.errors import ...`` (including lazy imports inside
    function bodies) that would silently rot if reintroduced.
    """
    offenders = [
        (path, module)
        for path in _python_files(_SRC)
        for module in _imported_modules(path)
        if module in _FORBIDDEN_MODULES
    ]
    if offenders:
        rendered = "\n".join(
            f"  {path.relative_to(_REPO)}: imports {module}"
            for path, module in offenders
        )
        msg = (
            "Imports of deleted error modules detected:\n"
            f"{rendered}\n"
            "Use synthorg.core.error_taxonomy / synthorg.core.domain_errors / "
            "synthorg.core.persistence_errors instead."
        )
        raise AssertionError(msg)


@pytest.mark.unit
def test_lower_layers_dont_reach_into_api_errors_namespace() -> None:
    """Lower layers may not import anything under ``synthorg.api.errors.*``.

    The whole point of #1610 is to break the upward edge from
    domain layers (engine, tools, budget, ...) into the API layer's
    error module.  Even though the module no longer exists today, this
    test pins the rule so it survives any future re-introduction.
    """
    offenders = [
        (path, module)
        for path in _python_files(_SRC)
        if path.relative_to(_SRC).parts[0] in _LOWER_LAYERS
        for module in _imported_modules(path)
        if module == "synthorg.api.errors" or module.startswith("synthorg.api.errors.")
    ]
    if offenders:
        rendered = "\n".join(
            f"  {path.relative_to(_REPO)}: imports {module}"
            for path, module in offenders
        )
        msg = (
            f"Lower-layer modules cannot import synthorg.api.errors.*:\n"
            f"{rendered}\n"
            "Move shared exceptions into synthorg.core.* instead."
        )
        raise AssertionError(msg)


@pytest.mark.unit
def test_controllers_use_core_persistence_errors() -> None:
    """Controllers under ``api/controllers`` route persistence errors through core.

    Phase 3 of #1610 moved the persistence error hierarchy into
    ``synthorg.core.persistence_errors``.  Controllers must import from
    core (the abstraction) rather than reaching back into the
    ``synthorg.persistence`` package internals.
    """
    controllers = _SRC / "api" / "controllers"
    offenders = [
        (path, module)
        for path in controllers.glob("*.py")
        for module in _imported_modules(path)
        if module == "synthorg.persistence.errors"
    ]
    if offenders:
        rendered = "\n".join(
            f"  {path.relative_to(_REPO)}: imports {module}"
            for path, module in offenders
        )
        msg = (
            f"Controllers must import from synthorg.core.persistence_errors:\n"
            f"{rendered}"
        )
        raise AssertionError(msg)


@pytest.mark.unit
def test_core_error_modules_are_leaf() -> None:
    """The three new ``core/*_errors`` modules import only stdlib + core.

    Pure leaf modules let the CLI and any future extension import error
    metadata without dragging in the API or persistence layers.
    """
    leaf_modules = [
        _SRC / "core" / "error_taxonomy.py",
        _SRC / "core" / "domain_errors.py",
        _SRC / "core" / "persistence_errors.py",
    ]
    allowed_prefixes = ("synthorg.core.",)
    for path in leaf_modules:
        for module in _imported_modules(path):
            if module.startswith("synthorg.") and not module.startswith(
                allowed_prefixes
            ):
                msg = (
                    f"{path.relative_to(_REPO)} imports {module}; core "
                    "error modules must depend only on stdlib and other "
                    "core modules."
                )
                raise AssertionError(msg)


@pytest.mark.unit
def test_layering_test_does_not_import_forbidden_modules() -> None:
    """Meta-guard: this test file itself must not regress to the legacy paths.

    A layering test that catches `from synthorg.api.errors import ...`
    elsewhere is useless if it accidentally imports the same path itself
    (e.g. via a refactor mistake).  Self-validate.
    """
    self_path = Path(__file__).resolve()
    for module in _imported_modules(self_path):
        assert module not in _FORBIDDEN_MODULES, (
            f"tests/unit/architecture/test_layering.py imports {module}; "
            "the layering guard must not depend on the very modules it forbids."
        )
