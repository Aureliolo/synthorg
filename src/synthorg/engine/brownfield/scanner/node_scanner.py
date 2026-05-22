"""Node.js ecosystem structure-map scanner.

Reads ``package.json`` for dependencies, ``main`` / ``bin`` entry points,
and the test framework; classifies the ecosystem as TypeScript when a
``tsconfig.json`` is present.
"""

import json
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
    top_level_dirs,
)
from synthorg.engine.brownfield.scanner.protocol import EcosystemScan

_SOURCE_DIRS: Final[tuple[str, ...]] = ("src", "lib", "app")
_TEST_DIRS: Final[tuple[str, ...]] = ("test", "tests", "__tests__")
_KNOWN_FRAMEWORKS: Final[tuple[str, ...]] = ("jest", "vitest", "mocha", "jasmine")


class NodeScanner:
    """Structure-map scanner for the Node.js ecosystem."""

    def ecosystem(self) -> Ecosystem:
        """Return the ``JAVASCRIPT`` discriminator (TS detected per-tree)."""
        return Ecosystem.JAVASCRIPT

    def detect(self, workspace_path: Path) -> bool:
        """True if a ``package.json`` sits at the tree root."""
        return (workspace_path / "package.json").is_file()

    def scan(self, workspace_path: Path) -> EcosystemScan:
        """Read ``package.json`` + tree and contribute Node structure facts."""
        manifest = self._load_manifest(workspace_path)
        language = (
            Ecosystem.TYPESCRIPT
            if (workspace_path / "tsconfig.json").is_file()
            else Ecosystem.JAVASCRIPT
        )
        return EcosystemScan(
            modules=self._modules(workspace_path, language),
            entry_points=self._entry_points(manifest),
            test_suites=self._test_suites(workspace_path, manifest),
            build_files=(BuildFile(path="package.json", tool="npm"),),
            dependencies=self._dependencies(manifest),
        )

    def _load_manifest(self, workspace_path: Path) -> dict[str, Any]:
        text = read_text_if_present(workspace_path / "package.json")
        if text is None:
            return {}
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _modules(self, workspace_path: Path, language: Ecosystem) -> tuple[Module, ...]:
        present = [d for d in _SOURCE_DIRS if d in top_level_dirs(workspace_path)]
        return tuple(
            Module(path=name, language=language, kind=ModuleKind.DIRECTORY)
            for name in present
        )

    def _entry_points(self, manifest: dict[str, Any]) -> tuple[EntryPoint, ...]:
        points: list[EntryPoint] = []
        main = manifest.get("main")
        if isinstance(main, str) and main:
            points.append(EntryPoint(path=main, kind=EntryPointKind.MAIN_MODULE))
        binary = manifest.get("bin")
        if isinstance(binary, str) and binary:
            points.append(EntryPoint(path=binary, kind=EntryPointKind.BINARY))
        elif isinstance(binary, dict):
            for name, target in sorted(binary.items()):
                if isinstance(target, str) and target:
                    points.append(
                        EntryPoint(
                            path=target,
                            kind=EntryPointKind.BINARY,
                            command=name,
                        )
                    )
        return tuple(points)

    def _test_suites(
        self, workspace_path: Path, manifest: dict[str, Any]
    ) -> tuple[TestSuite, ...]:
        framework = self._test_framework(manifest)
        present = [d for d in _TEST_DIRS if d in top_level_dirs(workspace_path)]
        return tuple(TestSuite(path=name, framework=framework) for name in present)

    def _test_framework(self, manifest: dict[str, Any]) -> str | None:
        dev = manifest.get("devDependencies", {})
        names = set(dev) if isinstance(dev, dict) else set()
        for framework in _KNOWN_FRAMEWORKS:
            if framework in names:
                return framework
        return None

    def _dependencies(self, manifest: dict[str, Any]) -> tuple[Dependency, ...]:
        deps: list[Dependency] = []
        deps.extend(
            self._parse_block(manifest.get("dependencies"), DependencyScope.RUNTIME)
        )
        deps.extend(
            self._parse_block(
                manifest.get("devDependencies"), DependencyScope.DEVELOPMENT
            )
        )
        return tuple(deps)

    def _parse_block(self, block: Any, scope: DependencyScope) -> list[Dependency]:
        if not isinstance(block, dict):
            return []
        parsed: list[Dependency] = []
        for name, version in sorted(block.items()):
            if not isinstance(name, str) or not name:
                continue
            spec = version if isinstance(version, str) and version else None
            parsed.append(
                Dependency(
                    name=name,
                    ecosystem=Ecosystem.JAVASCRIPT,
                    scope=scope,
                    version_spec=spec,
                )
            )
        return parsed
