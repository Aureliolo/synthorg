"""Python ecosystem structure-map scanner.

Reads ``pyproject.toml`` (PEP 621) for dependencies, console scripts, and
the test framework; discovers importable packages (dirs with
``__init__.py``) and ``__main__.py`` entry points by walking the tree.
"""

import re
import tomllib
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649 introspection)
from typing import Any, Final

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
    """Extract the package name from a PEP 508 requirement string."""
    match = _REQUIREMENT_NAME_RE.match(spec.strip())
    return match.group(0) if match else None


class PythonScanner:
    """Structure-map scanner for the Python ecosystem."""

    def ecosystem(self) -> Ecosystem:
        """Return the ``PYTHON`` discriminator."""
        return Ecosystem.PYTHON

    def detect(self, workspace_path: Path) -> bool:
        """True if any Python packaging manifest sits at the tree root."""
        return any((workspace_path / name).is_file() for name in _MANIFESTS)

    def scan(self, workspace_path: Path) -> EcosystemScan:
        """Read manifests + tree and contribute Python structure facts."""
        rel_paths = walk_relative_paths(workspace_path)
        pyproject = self._load_pyproject(workspace_path)
        return EcosystemScan(
            modules=self._modules(rel_paths),
            entry_points=self._entry_points(rel_paths, pyproject),
            test_suites=self._test_suites(workspace_path, rel_paths, pyproject),
            build_files=self._build_files(workspace_path),
            dependencies=self._dependencies(pyproject),
        )

    def _load_pyproject(self, workspace_path: Path) -> dict[str, Any]:
        text = read_text_if_present(workspace_path / "pyproject.toml")
        if text is None:
            return {}
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}

    def _build_files(self, workspace_path: Path) -> tuple[BuildFile, ...]:
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
        packages = sorted(
            {
                path.rsplit("/__init__.py", 1)[0]
                for path in rel_paths
                if path.endswith("__init__.py")
            }
        )
        return tuple(
            Module(path=pkg, language=Ecosystem.PYTHON, kind=ModuleKind.PACKAGE)
            for pkg in packages[:_MAX_PACKAGES]
        )

    def _entry_points(
        self, rel_paths: list[str], pyproject: dict[str, Any]
    ) -> tuple[EntryPoint, ...]:
        points: list[EntryPoint] = []
        scripts = pyproject.get("project", {}).get("scripts", {})
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
        pyproject: dict[str, Any],
    ) -> tuple[TestSuite, ...]:
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
        self, workspace_path: Path, pyproject: dict[str, Any]
    ) -> str | None:
        if "pytest" in str(pyproject.get("tool", {})):
            return "pytest"
        if (workspace_path / "pytest.ini").is_file():
            return "pytest"
        if (workspace_path / "conftest.py").is_file():
            return "pytest"
        return None

    def _dependencies(self, pyproject: dict[str, Any]) -> tuple[Dependency, ...]:
        project = pyproject.get("project", {})
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
        self, specs: list[Any], scope: DependencyScope
    ) -> list[Dependency]:
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
