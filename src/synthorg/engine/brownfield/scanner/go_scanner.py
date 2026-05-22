"""Go ecosystem structure-map scanner.

Reads ``go.mod`` for the module path and required dependencies, and walks
``*.go`` files to find ``package main`` directories (binary entry points)
and ``*_test.go`` locations (test suites).
"""

import re
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649 introspection)
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

_REQUIRE_RE: Final[re.Pattern[str]] = re.compile(
    # The path token must contain a slash so bare go.mod directives like
    # ``retract v1.2.3`` are not misread as require lines. Dots, hyphens
    # and pluses stay allowed inside each segment.
    r"^\s*([\w.\-+]+/[\w.\-+/]+)\s+(v[\w.\-+]+)",
    re.MULTILINE,
)
_MODULE_RE: Final[re.Pattern[str]] = re.compile(r"^module\s+(\S+)", re.MULTILINE)
_PACKAGE_MAIN_RE: Final[re.Pattern[str]] = re.compile(
    r"^package\s+main\b", re.MULTILINE
)
_MAX_GO_FILES: Final[int] = 2000


class GoScanner:
    """Structure-map scanner for the Go ecosystem."""

    def ecosystem(self) -> Ecosystem:
        """Return the ``GO`` discriminator."""
        return Ecosystem.GO

    def detect(self, workspace_path: Path) -> bool:
        """True if a ``go.mod`` sits at the tree root."""
        return (workspace_path / "go.mod").is_file()

    def scan(self, workspace_path: Path) -> EcosystemScan:
        """Read ``go.mod`` + ``*.go`` files and contribute Go structure facts."""
        gomod = read_text_if_present(workspace_path / "go.mod") or ""
        go_files = [p for p in walk_relative_paths(workspace_path) if p.endswith(".go")]
        return EcosystemScan(
            modules=self._modules(gomod),
            entry_points=self._entry_points(workspace_path, go_files),
            test_suites=self._test_suites(go_files),
            build_files=(BuildFile(path="go.mod", tool="go modules"),),
            dependencies=self._dependencies(gomod),
        )

    def _modules(self, gomod: str) -> tuple[Module, ...]:
        match = _MODULE_RE.search(gomod)
        if match is None:
            return ()
        return (
            Module(
                path=match.group(1),
                language=Ecosystem.GO,
                kind=ModuleKind.MODULE,
            ),
        )

    def _entry_points(
        self, workspace_path: Path, go_files: list[str]
    ) -> tuple[EntryPoint, ...]:
        main_dirs: set[str] = set()
        for rel in go_files[:_MAX_GO_FILES]:
            if rel.endswith("_test.go"):
                continue
            text = read_text_if_present(workspace_path / rel)
            if text is not None and _PACKAGE_MAIN_RE.search(text):
                parent = rel.rsplit("/", 1)[0] if "/" in rel else "."
                main_dirs.add(parent)
        return tuple(
            EntryPoint(path=path, kind=EntryPointKind.BINARY)
            for path in sorted(main_dirs)
        )

    def _test_suites(self, go_files: list[str]) -> tuple[TestSuite, ...]:
        dirs = sorted(
            {
                (rel.rsplit("/", 1)[0] if "/" in rel else ".")
                for rel in go_files
                if rel.endswith("_test.go")
            }
        )
        return tuple(TestSuite(path=path, framework="go test") for path in dirs)

    def _dependencies(self, gomod: str) -> tuple[Dependency, ...]:
        module = _MODULE_RE.search(gomod)
        module_path = module.group(1) if module else None
        seen: dict[str, Dependency] = {}
        for name, version in _REQUIRE_RE.findall(gomod):
            if name == module_path or name in seen:
                continue
            seen[name] = Dependency(
                name=name,
                ecosystem=Ecosystem.GO,
                scope=DependencyScope.RUNTIME,
                version_spec=version,
            )
        return tuple(seen[name] for name in sorted(seen))
