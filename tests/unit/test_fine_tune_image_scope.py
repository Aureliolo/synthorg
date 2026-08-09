"""The fine-tune image rebuilds when anything it executes changes.

``docker/fine-tune/Dockerfile`` copies the whole ``src/`` tree, but only
the entrypoint's import closure can change what the image does. The
build trigger is scoped to that closure rather than to ``src/**``,
because a fine-tune rebuild costs roughly 26 minutes of wall clock
across its two variants while the closure is 3% of the tree.

Scoping is only safe while the declared closure matches the real one, so
the closure is derived here from the entrypoint module rather than
maintained by hand: a new import that escapes the filter fails this
test instead of silently publishing a stale image.

Only executable imports count. An ``if TYPE_CHECKING:`` import never
runs, so the module it names cannot change what the image does; walking
those edges too reports 41% of the tree rather than 3%, almost all of it
unreachable at runtime.
"""

import ast
import tomllib
from pathlib import Path
from typing import Final

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_SRC_ROOT: Final[Path] = _REPO_ROOT / "src"
_WORKFLOW: Final[Path] = _REPO_ROOT / ".github" / "workflows" / "build-images.yml"

#: The image's ``ENTRYPOINT`` module, run as ``python -m``.
_ENTRYPOINT_MODULE: Final[str] = "synthorg.memory.embedding.fine_tune_runner"

#: The ``dorny/paths-filter`` key whose globs gate the fine-tune build.
_FILTER_NAME: Final[str] = "fine-tune"

#: The guard whose body a type checker reads and the interpreter skips.
_TYPE_CHECKING_GUARD: Final[str] = "TYPE_CHECKING"


def _module_file(module: str) -> Path | None:
    """Locate *module* inside ``src/``.

    Returns:
        The module's file, its package ``__init__``, or ``None`` when the
        name is not a module in this tree (a symbol, or a third party).
    """
    relative = Path(*module.split("."))
    for candidate in (
        _SRC_ROOT / relative.with_suffix(".py"),
        _SRC_ROOT / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def _absolute_module(node: ast.ImportFrom, containing: str) -> str | None:
    """Resolve *node*'s target to an absolute dotted module path.

    Returns:
        The absolute module the import reads from, or ``None`` when the
        relative level walks off the top of the package.
    """
    if node.level == 0:
        return node.module
    parts = containing.split(".")[: -node.level]
    if not parts:
        return None
    return ".".join([*parts, node.module] if node.module else parts)


def _type_checking_only(tree: ast.Module) -> frozenset[int]:
    """Identify nodes that only a type checker ever reads.

    Returns:
        ``id()`` of every node inside an ``if TYPE_CHECKING:`` body.
    """
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        name = (
            test.id
            if isinstance(test, ast.Name)
            else test.attr
            if isinstance(test, ast.Attribute)
            else None
        )
        if name != _TYPE_CHECKING_GUARD:
            continue
        guarded.update(id(inner) for stmt in node.body for inner in ast.walk(stmt))
    return frozenset(guarded)


def _imported_modules(tree: ast.Module, containing: str) -> set[str]:
    """Collect every ``synthorg`` module *tree* imports and executes.

    A function-level import counts, because it runs when the function
    does. A ``TYPE_CHECKING`` import does not, because it never runs.

    Returns:
        Dotted module paths, including ``from x import y`` where ``y``
        is itself a module.
    """
    guarded = _type_checking_only(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            found.update(
                alias.name for alias in node.names if alias.name.startswith("synthorg")
            )
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_module(node, containing)
            if module is None or not module.startswith("synthorg"):
                continue
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def _import_closure(entrypoint: str) -> frozenset[Path]:
    """Walk *entrypoint*'s transitive first-party import closure.

    Returns:
        Every file under ``src/`` the entrypoint can reach.
    """
    pending = [entrypoint]
    seen: set[str] = set()
    files: set[Path] = set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_file(module)
        if path is None:
            continue
        files.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        pending.extend(_imported_modules(tree, module) - seen)
    return frozenset(files)


def _fine_tune_filters() -> tuple[str, ...]:
    """Read the fine-tune path filter out of the images workflow.

    Returns:
        The glob patterns gating a fine-tune rebuild.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["changes"]["steps"]:
        raw = step.get("with", {}).get("filters")
        if raw is None:
            continue
        return tuple(yaml.safe_load(raw)[_FILTER_NAME])
    msg = "No paths-filter step found in the changes job"
    raise AssertionError(msg)


def _matches(pattern: str, path: str) -> bool:
    """Whether *pattern* selects *path*.

    Returns:
        True when the glob covers the path. Only the ``prefix/**`` and
        exact-path forms the workflow uses are recognised, so an
        unfamiliar pattern reads as no coverage rather than as coverage
        this cannot verify.
    """
    if pattern.endswith("/**"):
        return path.startswith(f"{pattern.removesuffix('/**')}/")
    return path == pattern


def _uncovered(files: frozenset[Path], filters: tuple[str, ...]) -> tuple[str, ...]:
    """Files the entrypoint reaches that no filter selects.

    Returns:
        Repository-relative paths, sorted.
    """
    return tuple(
        sorted(
            relative
            for relative in (file.relative_to(_REPO_ROOT).as_posix() for file in files)
            if not any(_matches(pattern, relative) for pattern in filters)
        )
    )


class TestFineTuneRebuildTrigger:
    """The trigger covers everything the entrypoint executes."""

    def test_entrypoint_matches_the_dockerfile(self) -> None:
        dockerfile = (_REPO_ROOT / "docker" / "fine-tune" / "Dockerfile").read_text(
            encoding="utf-8",
        )

        assert _ENTRYPOINT_MODULE in dockerfile

    def test_every_reachable_module_is_in_the_filter(self) -> None:
        closure = _import_closure(_ENTRYPOINT_MODULE)

        uncovered = _uncovered(closure, _fine_tune_filters())

        assert not uncovered, (
            "The fine-tune entrypoint imports source the rebuild trigger does "
            "not watch, so a change to it publishes nothing and the image "
            f"drifts from the tree: {uncovered}"
        )

    def test_dependency_changes_rebuild_the_image(self) -> None:
        """The lock pins what the image installs, so it gates a rebuild."""
        filters = _fine_tune_filters()

        assert "uv.lock" in filters
        assert "pyproject.toml" in filters

    def test_the_variants_the_filter_serves_still_exist(self) -> None:
        """The two build-arg variants the image ships are declared groups."""
        pyproject = tomllib.loads(
            (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        )
        groups = pyproject["dependency-groups"]

        assert "fine-tune-gpu" in groups
        assert "fine-tune-cpu" in groups
