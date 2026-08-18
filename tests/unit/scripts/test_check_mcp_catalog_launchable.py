"""Unit tests for ``scripts/check_mcp_catalog_launchable.py``.

The gate exists because nothing in the build noticed that the shipped stack
could not run the shipped catalog. The cases below are about the two ways that
returns: the image stops providing a runtime the declaration promises, and a
catalog entry starts naming a runtime nothing provides.

Tests load the script via :mod:`importlib` and drive ``main`` against a fake
tree, matching ``test_check_wave_dispatch_gated.py``.
"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_mcp_catalog_launchable.py"

_PROVISION_REL = "src/synthorg/tools/mcp/runtime_provision.py"
_APKO_REL = "docker/sandbox/apko.yaml"
_CATALOG_REL = "src/synthorg/integrations/mcp_catalog/bundled.json"
_INSTALL_REL = "src/synthorg/integrations/mcp_catalog/install.py"

_APKO = """contents:
  packages:
    - busybox
    - bash
    - git
    - nodejs-24
    - npm
    - python-3.14

accounts:
  run-as: "10001"
"""

_INSTALL = '''"""Installer stand-in."""

from typing import Final

_NPM_LAUNCHER: Final[str] = "npx"
'''


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_mcp_catalog_launchable",
            _SCRIPT_PATH,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()


def _provision(programs: dict[str, str]) -> str:
    """Render a runtime-provision module declaring *programs*.

    Returns:
        The module source.
    """
    entries = "\n".join(f'    "{k}": "{v}",' for k, v in programs.items())
    return (
        '"""Runtime provision stand-in."""\n\n'
        "from collections.abc import Mapping\n"
        "from typing import Final\n\n"
        f"RUNTIME_PROGRAMS: Final[Mapping[str, str]] = {{\n{entries}\n}}\n"
    )


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(
    tmp_path: Path,
    *,
    programs: dict[str, str] | None = None,
    apko: str = _APKO,
    entries: list[dict[str, object]] | None = None,
) -> Path:
    """Write a fake repository the gate can be pointed at.

    Returns:
        The project root.
    """
    declared = programs if programs is not None else {"npx": "npm", "node": "nodejs-24"}
    catalog = (
        entries
        if entries is not None
        else [
            {
                "id": "brave-search-mcp",
                "transport": "stdio",
                "npm_package": "@example/search-mcp-server",
                "npm_version": "2.1.0",
            }
        ]
    )
    _write(tmp_path, _PROVISION_REL, _provision(declared))
    _write(tmp_path, _APKO_REL, apko)
    _write(tmp_path, _CATALOG_REL, json.dumps({"servers": catalog}))
    _write(tmp_path, _INSTALL_REL, _INSTALL)
    return tmp_path


class TestTheImageMustProvideWhatIsDeclared:
    def test_a_declaration_the_image_backs_passes(self, tmp_path: Path) -> None:
        assert _MODULE.main(["--repo-root", str(_tree(tmp_path))]) == 0

    def test_a_dropped_package_is_reported(self, tmp_path: Path) -> None:
        """The regression that made the whole catalog unlaunchable.

        Removing ``npm`` from the image leaves every ``npx`` entry failing at
        connect on every boot, with nothing in the build to say so.
        """
        root = _tree(tmp_path, apko=_APKO.replace("    - npm\n", ""))
        assert _MODULE.main(["--repo-root", str(root)]) == 1

    def test_a_program_declared_via_an_absent_package_is_reported(
        self, tmp_path: Path
    ) -> None:
        root = _tree(tmp_path, programs={"uvx": "uv", "npx": "npm"})
        assert _MODULE.main(["--repo-root", str(root)]) == 1


class TestEveryEntryMustNameADeclaredProgram:
    def test_an_entry_whose_launcher_is_undeclared_is_reported(
        self, tmp_path: Path
    ) -> None:
        """A catalog entry is data, so nothing else in the build reads it."""
        root = _tree(tmp_path, programs={"node": "nodejs-24"})
        assert _MODULE.main(["--repo-root", str(root)]) == 1

    def test_a_remote_entry_needs_no_local_runtime(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            programs={"node": "nodejs-24"},
            entries=[{"id": "hosted-thing", "transport": "streamable_http"}],
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 0

    @pytest.mark.parametrize("absent", ["npm_package", "npm_version"])
    def test_a_stdio_entry_missing_an_npm_field_is_a_configuration_error(
        self, tmp_path: Path, absent: str
    ) -> None:
        """The transport alone does not say an entry can launch.

        ``installation_to_server_config`` refuses a stdio entry missing either
        field, so reading the launcher off the transport would let the gate
        certify an entry the installer rejects at install time.
        """
        entry: dict[str, object] = {
            "id": "brave-search-mcp",
            "transport": "stdio",
            "npm_package": "@example/search-mcp-server",
            "npm_version": "2.1.0",
        }
        del entry[absent]
        root = _tree(tmp_path, entries=[entry])

        assert _MODULE.main(["--repo-root", str(root)]) == 2


class TestFailClosed:
    def test_an_empty_declaration_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        """A gate with nothing to enforce must say so, not pass."""
        _write(tmp_path, _PROVISION_REL, _provision({}).replace("\n\n}", "}"))
        _write(tmp_path, _APKO_REL, _APKO)
        _write(tmp_path, _CATALOG_REL, json.dumps({"servers": []}))
        _write(tmp_path, _INSTALL_REL, _INSTALL)
        assert _MODULE.main(["--repo-root", str(tmp_path)]) == 2

    def test_an_apko_file_with_no_packages_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        root = _tree(tmp_path, apko='accounts:\n  run-as: "10001"\n')
        assert _MODULE.main(["--repo-root", str(root)]) == 2

    def test_an_empty_catalog_is_a_configuration_error(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, entries=[])
        assert _MODULE.main(["--repo-root", str(root)]) == 2

    def test_a_missing_repo_root_is_a_configuration_error(self, tmp_path: Path) -> None:
        assert _MODULE.main(["--repo-root", str(tmp_path / "absent")]) == 2

    def test_an_installer_without_its_launcher_constant_is_reported(
        self, tmp_path: Path
    ) -> None:
        root = _tree(tmp_path)
        _write(root, _INSTALL_REL, '"""No launcher here."""\n')
        assert _MODULE.main(["--repo-root", str(root)]) == 2

    def test_a_computed_launcher_is_a_configuration_error(self, tmp_path: Path) -> None:
        """A launcher the gate cannot read is the same gap as an absent one."""
        root = _tree(tmp_path)
        _write(
            root,
            _INSTALL_REL,
            '"""Installer stand-in."""\n\n'
            "from typing import Final\n\n"
            "_NPM_LAUNCHER: Final[str] = _resolve()\n",
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 2

    @pytest.mark.parametrize("literal", ["0", '""', '"   "', '("npx",)'])
    def test_a_launcher_that_is_not_a_usable_string_is_reported(
        self, tmp_path: Path, literal: str
    ) -> None:
        """Coercing these to text mints a launcher name nobody could run.

        ``str(0)`` is ``'0'`` and ``str(("npx",))`` contains ``npx``: both are
        values a declaration could match, so the gate would pass on a constant
        that launches nothing.
        """
        root = _tree(tmp_path)
        _write(
            root,
            _INSTALL_REL,
            '"""Installer stand-in."""\n\n'
            "from typing import Final\n\n"
            f"_NPM_LAUNCHER: Final[str] = {literal}\n",
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 2


class TestRealTree:
    """The shipped catalog is launchable by the shipped image."""

    def test_live_tree_is_clean(self) -> None:
        assert _MODULE.main(["--repo-root", str(_REPO_ROOT)]) == 0
