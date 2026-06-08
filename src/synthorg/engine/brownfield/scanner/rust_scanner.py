"""Rust ecosystem structure-map scanner.

Reads ``Cargo.toml`` for the crate name, dependencies (runtime / dev /
build), and explicit ``[[bin]]`` targets; treats ``src/main.rs`` as a
binary entry point and ``src/lib.rs`` as a library module.
"""

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
from synthorg.engine.brownfield.scanner._common import read_text_if_present
from synthorg.engine.brownfield.scanner.protocol import EcosystemScan

_DEP_TABLES: Final[tuple[tuple[str, DependencyScope], ...]] = (
    ("dependencies", DependencyScope.RUNTIME),
    ("dev-dependencies", DependencyScope.DEVELOPMENT),
    ("build-dependencies", DependencyScope.BUILD),
)


class RustScanner:
    """Structure-map scanner for the Rust ecosystem."""

    def ecosystem(self) -> Ecosystem:
        """Return the ``RUST`` discriminator."""
        return Ecosystem.RUST

    def detect(self, workspace_path: Path) -> bool:
        """True if a ``Cargo.toml`` sits at the tree root.

        Returns:
            ``True`` when ``workspace_path / "Cargo.toml"`` exists.
        """
        return (workspace_path / "Cargo.toml").is_file()

    def scan(self, workspace_path: Path) -> EcosystemScan:
        """Read ``Cargo.toml`` + tree and contribute Rust structure facts.

        Returns:
            An :class:`EcosystemScan` carrying the crate modules,
            binary entry points, test directories, the
            ``Cargo.toml`` build file, and declared dependencies.
        """
        cargo = self._load_cargo(workspace_path)
        return EcosystemScan(
            modules=self._modules(workspace_path, cargo),
            entry_points=self._entry_points(workspace_path, cargo),
            test_suites=self._test_suites(workspace_path),
            build_files=(BuildFile(path="Cargo.toml", tool="cargo"),),
            dependencies=self._dependencies(cargo),
        )

    def _load_cargo(self, workspace_path: Path) -> dict[str, object]:
        """Parse ``Cargo.toml`` at the tree root.

        Args:
            workspace_path: Codebase root being scanned.

        Returns:
            The parsed TOML table, or an empty dict when the file is
            absent or malformed.
        """
        text = read_text_if_present(workspace_path / "Cargo.toml")
        if text is None:
            return {}
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return {}

    def _modules(
        self, workspace_path: Path, cargo: dict[str, object]
    ) -> tuple[Module, ...]:
        """Map ``src/lib.rs`` to a crate module when present.

        Args:
            workspace_path: Codebase root being scanned.
            cargo: Parsed ``Cargo.toml`` table.

        Returns:
            A single-element tuple for ``src/lib.rs`` (package kind when
            the crate is named), or ``()`` when there is no library root.
        """
        if not (workspace_path / "src" / "lib.rs").is_file():
            return ()
        package = cargo.get("package", {})
        name = package.get("name") if isinstance(package, dict) else None
        return (
            Module(
                path="src/lib.rs",
                language=Ecosystem.RUST,
                kind=ModuleKind.PACKAGE if name else ModuleKind.MODULE,
            ),
        )

    def _entry_points(
        self, workspace_path: Path, cargo: dict[str, object]
    ) -> tuple[EntryPoint, ...]:
        """Collect binary entry points from ``src/main.rs`` and ``[[bin]]``.

        Args:
            workspace_path: Codebase root being scanned.
            cargo: Parsed ``Cargo.toml`` table.

        Returns:
            A binary :class:`EntryPoint` for ``src/main.rs`` (when present)
            and for each ``[[bin]]`` target with a path.
        """
        points: list[EntryPoint] = []
        if (workspace_path / "src" / "main.rs").is_file():
            points.append(EntryPoint(path="src/main.rs", kind=EntryPointKind.BINARY))
        bins = cargo.get("bin", [])
        if isinstance(bins, list):
            for entry in bins:
                if not isinstance(entry, dict):
                    continue
                path = entry.get("path")
                name = entry.get("name")
                if isinstance(path, str) and path:
                    points.append(
                        EntryPoint(
                            path=path,
                            kind=EntryPointKind.BINARY,
                            command=name if isinstance(name, str) and name else None,
                        )
                    )
        return tuple(points)

    def _test_suites(self, workspace_path: Path) -> tuple[TestSuite, ...]:
        """Map the conventional ``tests/`` directory to a test suite.

        Args:
            workspace_path: Codebase root being scanned.

        Returns:
            A single ``cargo test`` :class:`TestSuite` when ``tests/``
            exists, else ``()``.
        """
        if (workspace_path / "tests").is_dir():
            return (TestSuite(path="tests", framework="cargo test"),)
        return ()

    def _dependencies(self, cargo: dict[str, object]) -> tuple[Dependency, ...]:
        """Collect runtime / dev / build dependencies from ``Cargo.toml``.

        Args:
            cargo: Parsed ``Cargo.toml`` table.

        Returns:
            A :class:`Dependency` per entry across the tables in
            :data:`_DEP_TABLES`.
        """
        deps: list[Dependency] = []
        for table, scope in _DEP_TABLES:
            block = cargo.get(table, {})
            if not isinstance(block, dict):
                continue
            for name, spec in sorted(block.items()):
                if not isinstance(name, str) or not name:
                    continue
                deps.append(
                    Dependency(
                        name=name,
                        ecosystem=Ecosystem.RUST,
                        scope=scope,
                        version_spec=self._version_of(spec),
                    )
                )
        return tuple(deps)

    def _version_of(self, spec: object) -> str | None:
        """Extract a version string from a Cargo dependency spec.

        Args:
            spec: Either a bare version string or a table with a
                ``version`` key.

        Returns:
            The version string, or ``None`` when none is declared.
        """
        if isinstance(spec, str) and spec:
            return spec
        if isinstance(spec, dict):
            version = spec.get("version")
            if isinstance(version, str) and version:
                return version
        return None
