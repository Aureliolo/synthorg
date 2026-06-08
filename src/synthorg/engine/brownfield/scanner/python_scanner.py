"""Python ecosystem structure-map scanner.

Reads ``pyproject.toml`` (PEP 621) for dependencies, console scripts, and
the test framework; discovers importable packages (dirs with
``__init__.py``) and ``__main__.py`` entry points by walking the tree.
"""

import re
import tomllib
from pathlib import Path
from typing import Final

from synthorg.core.codebase_structure_map import (
    BuildFile,
    Dependency,
    DependencyScope,
    Ecosystem,
    EntryPoint,
    EntryPointKind,
    Module,
    ModuleKind,
    TestSuite,
)
from synthorg.engine.brownfield.scanner._common import (
    read_text_if_present,
    walk_relative_paths,
)
from synthorg.engine.brownfield.scanner.protocol import EcosystemScan

_MANIFESTS: Final[tuple[str, ...]] = ("pyproject.toml", "setup.py", "setup.cfg")
_MAX_PACKAGES: Final[int] = 500
_REQUIREMENT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+")


def _requirement_name(spec: str) -> str | None:
    """Extract the package name from a PEP 508 requirement string.

    Returns:
        The matched distribution name, or ``None`` when the spec
        starts with a non-name token.
    """
    match = _REQUIREMENT_NAME_RE.match(spec.strip())
    return match.group(0) if match else None


class PythonScanner:
    """Structure-map scanner for the Python ecosystem."""

    def ecosystem(self) -> Ecosystem:
        """Return the ``PYTHON`` discriminator."""
        return Ecosystem.PYTHON

    def detect(self, workspace_path: Path) -> bool:
        """True if any Python packaging manifest sits at the tree root.

        Returns:
            ``True`` when any of :data:`_MANIFESTS` exists at the
            workspace root; ``False`` otherwise.
        """
        return any((workspace_path / name).is_file() for name in _MANIFESTS)

    def scan(self, workspace_path: Path) -> EcosystemScan:
        """Read manifests + tree and contribute Python structure facts.

        Returns:
            An :class:`EcosystemScan` carrying packages discovered
            under the tree, declared script / module entry points,
            test directories, build files, and declared dependencies.
        """
        rel_paths = walk_relative_paths(workspace_path)
        pyproject = self._load_pyproject(workspace_path)
        return EcosystemScan(
            modules=self._modules(rel_paths),
            entry_points=self._entry_points(rel_paths, pyproject),
            test_suites=self._test_suites(workspace_path, rel_paths, pyproject),
            build_files=self._build_files(workspace_path),
            dependencies=self._dependencies(pyproject),
        )

    def _load_pyproject(self, workspace_path: Path) -> dict[str, object]:
        """Parse ``pyproject.toml`` at the tree root.

        Args:
            workspace_path: Codebase root being scanned.

        Returns:
            The parsed TOML table, or an empty dict when the file is
            absent or malformed.
        """
        text = read_text_if_present(workspace_path / "pyproject.toml")
        if text is None:
            return {}
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}

    def _build_files(self, workspace_path: Path) -> tuple[BuildFile, ...]:
        """Identify packaging manifests present at the tree root.

        Args:
            workspace_path: Codebase root being scanned.

        Returns:
            A :class:`BuildFile` per manifest in :data:`_MANIFESTS` that
            exists at the root, tagged with its build tool.
        """
        tools = {
            "pyproject.toml": "pyproject",
            "setup.py": "setuptools",
            "setup.cfg": "setuptools",
        }
        return tuple(
            BuildFile(path=name, tool=tools[name])
            for name in _MANIFESTS
            if (workspace_path / name).is_file()
        )

    def _modules(self, rel_paths: list[str]) -> tuple[Module, ...]:
        """Discover importable packages from ``__init__.py`` locations.

        Args:
            rel_paths: Tree-relative file paths in the workspace.

        Returns:
            A :class:`Module` per package directory, capped at
            :data:`_MAX_PACKAGES`.
        """
        packages = sorted(
            {
                path.removesuffix("/__init__.py") if "/" in path else "."
                for path in rel_paths
                if path.endswith("__init__.py")
            }
        )
        return tuple(
            Module(path=pkg, language=Ecosystem.PYTHON, kind=ModuleKind.PACKAGE)
            for pkg in packages[:_MAX_PACKAGES]
        )

    def _entry_points(
        self, rel_paths: list[str], pyproject: dict[str, object]
    ) -> tuple[EntryPoint, ...]:
        """Collect console-script and ``__main__`` entry points.

        Args:
            rel_paths: Tree-relative file paths in the workspace.
            pyproject: Parsed ``pyproject.toml`` table.

        Returns:
            An :class:`EntryPoint` per declared console script and per
            ``__main__.py`` module found in the tree.
        """
        points: list[EntryPoint] = []
        project = pyproject.get("project")
        if not isinstance(project, dict):
            project = {}
        scripts = project.get("scripts", {})
        if isinstance(scripts, dict):
            for name, target in sorted(scripts.items()):
                points.append(
                    EntryPoint(
                        path="pyproject.toml",
                        kind=EntryPointKind.CONSOLE_SCRIPT,
                        command=f"{name} = {target}",
                    )
                )
        points.extend(
            EntryPoint(path=path, kind=EntryPointKind.MAIN_MODULE)
            for path in rel_paths
            if path.endswith("__main__.py")
        )
        return tuple(points)

    def _test_suites(
        self,
        workspace_path: Path,
        rel_paths: list[str],
        pyproject: dict[str, object],
    ) -> tuple[TestSuite, ...]:
        """Locate test roots and tag them with the detected framework.

        Args:
            workspace_path: Codebase root being scanned.
            rel_paths: Tree-relative file paths in the workspace.
            pyproject: Parsed ``pyproject.toml`` table.

        Returns:
            A :class:`TestSuite` per ``tests/`` / ``test/`` root.
        """
        framework = self._test_framework(workspace_path, pyproject)
        roots = sorted(
            {
                path.split("/", 1)[0]
                for path in rel_paths
                if path.startswith(("tests/", "test/"))
            }
        )
        return tuple(TestSuite(path=root, framework=framework) for root in roots)

    def _test_framework(
        self, workspace_path: Path, pyproject: dict[str, object]
    ) -> str | None:
        """Detect the test framework from config markers.

        Args:
            workspace_path: Codebase root being scanned.
            pyproject: Parsed ``pyproject.toml`` table.

        Returns:
            ``"pytest"`` when a pytest marker is present; ``None`` when
            no framework can be inferred.
        """
        tool_table = pyproject.get("tool", {})
        if isinstance(tool_table, dict) and "pytest" in tool_table:
            return "pytest"
        if (workspace_path / "pytest.ini").is_file():
            return "pytest"
        if (workspace_path / "conftest.py").is_file():
            return "pytest"
        return None

    def _dependencies(self, pyproject: dict[str, object]) -> tuple[Dependency, ...]:
        """Collect runtime and optional dependencies from ``pyproject``.

        Args:
            pyproject: Parsed ``pyproject.toml`` table.

        Returns:
            A :class:`Dependency` per declared runtime and optional
            requirement.
        """
        project = pyproject.get("project")
        if not isinstance(project, dict):
            project = {}
        deps: list[Dependency] = []
        runtime = project.get("dependencies", [])
        if isinstance(runtime, list):
            deps.extend(self._parse_specs(runtime, DependencyScope.RUNTIME))
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    deps.extend(self._parse_specs(group, DependencyScope.OPTIONAL))
        return tuple(deps)

    def _parse_specs(
        self, specs: list[object], scope: DependencyScope
    ) -> list[Dependency]:
        """Parse PEP 508 requirement strings into dependencies.

        Args:
            specs: Raw requirement entries (non-strings are skipped).
            scope: Dependency scope to tag each parsed entry with.

        Returns:
            A :class:`Dependency` per spec whose distribution name parses.
        """
        parsed: list[Dependency] = []
        for spec in specs:
            if not isinstance(spec, str):
                continue
            name = _requirement_name(spec)
            if name is None:
                continue
            version = spec[len(name) :].strip() or None
            parsed.append(
                Dependency(
                    name=name,
                    ecosystem=Ecosystem.PYTHON,
                    scope=scope,
                    version_spec=version,
                )
            )
        return parsed
