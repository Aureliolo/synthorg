"""Generic file-tree fallback scanner.

The safe default: used by the aggregator only when no ecosystem-specific
scanner matched. Contributes top-level directories as modules and
well-known root build/automation files, with no dependency facts (none
can be inferred without an ecosystem manifest).
"""

from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649 introspection)
from typing import Final

from synthorg.core.codebase_structure_map import (
    BuildFile,
    Ecosystem,
    Module,
    ModuleKind,
    TestSuite,
)
from synthorg.engine.brownfield.scanner._common import top_level_dirs
from synthorg.engine.brownfield.scanner.protocol import EcosystemScan

_KNOWN_BUILD_FILES: Final[tuple[tuple[str, str], ...]] = (
    ("Makefile", "make"),
    ("makefile", "make"),
    ("Dockerfile", "docker"),
    ("docker-compose.yml", "docker-compose"),
    ("docker-compose.yaml", "docker-compose"),
    ("CMakeLists.txt", "cmake"),
    ("build.gradle", "gradle"),
    ("pom.xml", "maven"),
)
_TEST_DIRS: Final[tuple[str, ...]] = ("test", "tests")


class GenericScanner:
    """Ecosystem-agnostic fallback structure-map scanner."""

    def ecosystem(self) -> Ecosystem:
        """Return the ``GENERIC`` discriminator."""
        return Ecosystem.GENERIC

    def detect(self, workspace_path: Path) -> bool:  # noqa: ARG002 -- always available
        """Always ``True``: the generic scanner is the universal fallback.

        Returns:
            ``True`` unconditionally so the aggregator can fall back
            to this scanner when no specific scanner matched.
        """
        return True

    def scan(self, workspace_path: Path) -> EcosystemScan:
        """Contribute top-level directories and well-known build files.

        Returns:
            An :class:`EcosystemScan` listing top-level directories
            (as ``DIRECTORY`` modules), recognised root build files,
            and ``test``/``tests`` directories as test suites.
        """
        dirs = top_level_dirs(workspace_path)
        modules = tuple(
            Module(path=name, language=Ecosystem.GENERIC, kind=ModuleKind.DIRECTORY)
            for name in dirs
            if name not in _TEST_DIRS
        )
        build_files = tuple(
            BuildFile(path=name, tool=tool)
            for name, tool in _KNOWN_BUILD_FILES
            if (workspace_path / name).is_file()
        )
        test_suites = tuple(TestSuite(path=name) for name in _TEST_DIRS if name in dirs)
        return EcosystemScan(
            modules=modules,
            build_files=build_files,
            test_suites=test_suites,
        )
