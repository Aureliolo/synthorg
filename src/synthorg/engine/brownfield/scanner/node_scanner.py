"""Node.js ecosystem structure-map scanner.

Reads ``package.json`` for dependencies, ``main`` / ``bin`` entry points,
and the test framework; classifies the ecosystem as TypeScript when a
``tsconfig.json`` is present.
"""

import json
from pathlib import Path
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
        """True if a ``package.json`` sits at the tree root.

        Returns:
            ``True`` when ``workspace_path / "package.json"`` exists.
        """
        return (workspace_path / "package.json").is_file()

    def scan(self, workspace_path: Path) -> EcosystemScan:
        """Read ``package.json`` + tree and contribute Node structure facts.

        Returns:
            An :class:`EcosystemScan` carrying modules, declared
            entry points, test suites, the ``package.json`` build
            file, and the declared runtime / dev dependencies.
        """
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
        """Parse ``package.json`` at the tree root.

        Args:
            workspace_path: Codebase root being scanned.

        Returns:
            The parsed manifest object, or an empty dict when the file
            is absent, unparseable, or not a JSON object.
        """
        text = read_text_if_present(workspace_path / "package.json")
        if text is None:
            return {}
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _modules(self, workspace_path: Path, language: Ecosystem) -> tuple[Module, ...]:
        """Map conventional source directories to modules.

        Args:
            workspace_path: Codebase root being scanned.
            language: Ecosystem discriminator (JS or TS) for each module.

        Returns:
            A :class:`Module` per present directory in :data:`_SOURCE_DIRS`.
        """
        present = [d for d in _SOURCE_DIRS if d in top_level_dirs(workspace_path)]
        return tuple(
            Module(path=name, language=language, kind=ModuleKind.DIRECTORY)
            for name in present
        )

    def _entry_points(self, manifest: dict[str, Any]) -> tuple[EntryPoint, ...]:
        """Collect ``main`` and ``bin`` entry points from the manifest.

        Args:
            manifest: Parsed ``package.json`` object.

        Returns:
            An :class:`EntryPoint` for the ``main`` module and for each
            ``bin`` target (string or name-to-target map).
        """
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
        """Locate test directories and tag them with the framework.

        Args:
            workspace_path: Codebase root being scanned.
            manifest: Parsed ``package.json`` object.

        Returns:
            A :class:`TestSuite` per present directory in :data:`_TEST_DIRS`.
        """
        framework = self._test_framework(manifest)
        present = [d for d in _TEST_DIRS if d in top_level_dirs(workspace_path)]
        return tuple(TestSuite(path=name, framework=framework) for name in present)

    def _test_framework(self, manifest: dict[str, Any]) -> str | None:
        """Detect the test framework from declared dependencies.

        Args:
            manifest: Parsed ``package.json`` object.

        Returns:
            The first matching name in :data:`_KNOWN_FRAMEWORKS`, or
            ``None`` when none is declared.
        """
        names: set[str] = set()
        for block in (manifest.get("dependencies"), manifest.get("devDependencies")):
            if isinstance(block, dict):
                names.update(block)
        for framework in _KNOWN_FRAMEWORKS:
            if framework in names:
                return framework
        return None

    def _dependencies(self, manifest: dict[str, Any]) -> tuple[Dependency, ...]:
        """Collect runtime and dev dependencies from the manifest.

        Args:
            manifest: Parsed ``package.json`` object.

        Returns:
            A :class:`Dependency` per ``dependencies`` and
            ``devDependencies`` entry.
        """
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
        """Parse one dependency block (name-to-version map).

        Args:
            block: A ``package.json`` dependency map (non-dicts yield ``[]``).
            scope: Dependency scope to tag each parsed entry with.

        Returns:
            A :class:`Dependency` per named entry in the block.
        """
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
