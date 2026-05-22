"""Unit tests for the structure-map scanners and aggregator."""

from pathlib import Path

import pytest

from synthorg.core.codebase_structure_map import (
    DependencyScope,
    Ecosystem,
    EntryPointKind,
    ModuleKind,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield.scanner import (
    BrownfieldScanConfig,
    build_structure_map_scanners,
    scan_codebase,
)
from synthorg.engine.brownfield.scanner.generic_scanner import GenericScanner
from synthorg.engine.brownfield.scanner.go_scanner import GoScanner
from synthorg.engine.brownfield.scanner.node_scanner import NodeScanner
from synthorg.engine.brownfield.scanner.python_scanner import PythonScanner
from synthorg.engine.brownfield.scanner.rust_scanner import RustScanner
from tests._shared import FakeClock

pytestmark = pytest.mark.unit


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestPythonScanner:
    def test_reads_pyproject_and_packages(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "pyproject.toml",
            """
[project]
name = "demo"
dependencies = ["httpx>=0.27", "pydantic"]
[project.optional-dependencies]
dev = ["pytest>=8"]
[project.scripts]
demo = "demo.cli:main"
[tool.pytest.ini_options]
addopts = "-q"
""",
        )
        _write(tmp_path / "demo" / "__init__.py", "")
        _write(tmp_path / "demo" / "__main__.py", "")
        _write(tmp_path / "tests" / "test_demo.py", "")

        scan = PythonScanner().scan(tmp_path)

        names = {d.name: d for d in scan.dependencies}
        assert names["httpx"].version_spec == ">=0.27"
        assert names["httpx"].scope is DependencyScope.RUNTIME
        assert names["pytest"].scope is DependencyScope.OPTIONAL
        assert any(
            m.path == "demo" and m.kind is ModuleKind.PACKAGE for m in scan.modules
        )
        kinds = {e.kind for e in scan.entry_points}
        assert EntryPointKind.CONSOLE_SCRIPT in kinds
        assert EntryPointKind.MAIN_MODULE in kinds
        assert scan.test_suites[0].framework == "pytest"
        assert {b.tool for b in scan.build_files} == {"pyproject"}

    def test_detect_false_without_manifest(self, tmp_path: Path) -> None:
        assert PythonScanner().detect(tmp_path) is False


class TestNodeScanner:
    def test_reads_package_json(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "package.json",
            """
{
  "name": "demo",
  "main": "src/index.js",
  "bin": {"demo": "bin/cli.js"},
  "dependencies": {"express": "^4.0.0"},
  "devDependencies": {"jest": "^29"}
}
""",
        )
        _write(tmp_path / "tsconfig.json", "{}")
        _write(tmp_path / "src" / "index.js", "")

        scan = NodeScanner().scan(tmp_path)

        assert any(m.language is Ecosystem.TYPESCRIPT for m in scan.modules)
        names = {d.name: d for d in scan.dependencies}
        assert names["express"].scope is DependencyScope.RUNTIME
        assert names["jest"].scope is DependencyScope.DEVELOPMENT
        assert any(e.kind is EntryPointKind.MAIN_MODULE for e in scan.entry_points)
        assert any(e.kind is EntryPointKind.BINARY for e in scan.entry_points)


class TestGoScanner:
    def test_reads_go_mod_and_main(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "go.mod",
            """module github.com/acme/demo

go 1.22

require (
\tgithub.com/spf13/cobra v1.8.0
\tgithub.com/stretchr/testify v1.9.0
)
""",
        )
        _write(tmp_path / "cmd" / "demo" / "main.go", "package main\nfunc main() {}\n")
        _write(tmp_path / "internal" / "svc_test.go", "package svc\n")

        scan = GoScanner().scan(tmp_path)

        assert scan.modules[0].path == "github.com/acme/demo"
        dep_names = {d.name for d in scan.dependencies}
        assert "github.com/spf13/cobra" in dep_names
        assert "github.com/acme/demo" not in dep_names
        assert any(
            e.kind is EntryPointKind.BINARY and e.path == "cmd/demo"
            for e in scan.entry_points
        )
        assert any(t.framework == "go test" for t in scan.test_suites)


class TestRustScanner:
    def test_reads_cargo(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "Cargo.toml",
            """
[package]
name = "demo"
[dependencies]
serde = "1.0"
[dev-dependencies]
proptest = { version = "1.4" }
""",
        )
        _write(tmp_path / "src" / "main.rs", "fn main() {}")

        scan = RustScanner().scan(tmp_path)

        names = {d.name: d for d in scan.dependencies}
        assert names["serde"].version_spec == "1.0"
        assert names["proptest"].scope is DependencyScope.DEVELOPMENT
        assert any(e.kind is EntryPointKind.BINARY for e in scan.entry_points)


class TestGenericScanner:
    def test_top_level_dirs_as_modules(self, tmp_path: Path) -> None:
        _write(tmp_path / "Makefile", "all:\n")
        _write(tmp_path / "core" / "thing.txt", "x")
        _write(tmp_path / "tests" / "case.txt", "x")

        scan = GenericScanner().scan(tmp_path)

        assert any(
            m.path == "core" and m.kind is ModuleKind.DIRECTORY for m in scan.modules
        )
        # test dirs are reported as suites, not generic modules
        assert all(m.path != "tests" for m in scan.modules)
        assert any(t.path == "tests" for t in scan.test_suites)
        assert {b.tool for b in scan.build_files} == {"make"}


class TestAggregator:
    async def test_specific_scanner_suppresses_generic(self, tmp_path: Path) -> None:
        _write(tmp_path / "pyproject.toml", '[project]\nname = "demo"\n')
        _write(tmp_path / "demo" / "__init__.py", "")
        _write(tmp_path / "extra" / "note.txt", "x")

        result = await scan_codebase(
            workspace_path=tmp_path,
            project_id=NotBlankStr("p1"),
            source_ref=NotBlankStr(str(tmp_path)),
            scanners=build_structure_map_scanners(),
            clock=FakeClock(),
        )

        # Python matched, so generic directory modules are NOT included.
        assert all(m.language is not Ecosystem.GENERIC for m in result.modules)
        assert any(m.path == "demo" for m in result.modules)

    async def test_generic_fallback_when_no_manifest(self, tmp_path: Path) -> None:
        _write(tmp_path / "src" / "thing.txt", "x")

        result = await scan_codebase(
            workspace_path=tmp_path,
            project_id=NotBlankStr("p1"),
            source_ref=NotBlankStr(str(tmp_path)),
            scanners=build_structure_map_scanners(),
            clock=FakeClock(),
        )

        assert any(m.language is Ecosystem.GENERIC for m in result.modules)

    async def test_content_hash_is_stable_across_rescans(self, tmp_path: Path) -> None:
        _write(tmp_path / "pyproject.toml", '[project]\nname = "demo"\n')
        _write(tmp_path / "demo" / "__init__.py", "")
        scanners = build_structure_map_scanners()

        first = await scan_codebase(
            workspace_path=tmp_path,
            project_id=NotBlankStr("p1"),
            source_ref=NotBlankStr(str(tmp_path)),
            scanners=scanners,
            clock=FakeClock(),
        )
        second = await scan_codebase(
            workspace_path=tmp_path,
            project_id=NotBlankStr("p2"),
            source_ref=NotBlankStr("other"),
            scanners=scanners,
            clock=FakeClock(),
        )

        # Hash covers structural facts only, independent of project/source/time.
        assert first.content_hash == second.content_hash

    def test_disabled_ecosystem_excluded(self) -> None:
        config = BrownfieldScanConfig(enabled_ecosystems=(Ecosystem.PYTHON,))
        scanners = build_structure_map_scanners(config)
        ecosystems = {s.ecosystem() for s in scanners}
        assert Ecosystem.PYTHON in ecosystems
        assert Ecosystem.GO not in ecosystems
        # Generic fallback is always present.
        assert Ecosystem.GENERIC in ecosystems
        assert any(isinstance(s, GenericScanner) for s in scanners)
