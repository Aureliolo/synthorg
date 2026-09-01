"""Unit tests for ``scripts/check_engine_dependencies_total.py``.

The gate guards one invariant: an engine's wiring is declared in full, so a
partially wired engine is not constructable. The shapes below are the ways that
contract is lost past a type-checker, written out verbatim, plus the near
neighbours that must NOT be flagged and the ways the gate's own anchors can be
pulled out from under it.

Every run is against a sandbox tree under ``tmp_path``. Pointing the gate at
the real repository would make these tests assert about whatever the working
copy happens to hold.
"""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every ``GIT_*`` env var for the duration of each test.

    The gate's ``git ls-files`` subprocess inherits this process's
    environment. Under a pre-push hook ``GIT_DIR`` / ``GIT_WORK_TREE`` point
    at the real repo, which would let the scan escape the ``tmp_path``
    sandbox and read the live tree.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_engine_dependencies_total.py"


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _PACKAGE_REL: str
    _ENGINE_REL: str
    _SANCTIONED_REL: str
    _SATELLITE_TYPES: dict[str, str]

    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    # The gate prepends scripts/ to sys.path at import time (to resolve its
    # _gate_source sibling); restore sys.path so the load leaves no global
    # side effect that could shadow an unrelated import.
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_engine_dependencies_total", _SCRIPT_PATH
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cast(_ScriptModule, module)
    finally:
        sys.path[:] = saved


_MODULE = _load_script()

_BUNDLE = '''"""A bundle."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineCore:
    """The provider and the clock."""

    provider: object
    clock: object
'''

_BUNDLE_WITH_DEFAULT = '''"""A bundle that grew a default back."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineCore:
    """The provider and the clock."""

    provider: object
    clock: object = None
'''

_ROOT = '''"""The root declaration."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineDependencies:
    """One engine's whole wiring."""

    core: object
'''

_WIRING = '''"""Checkpointing, both halves or neither."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckpointWiring:
    """Both repositories and their config."""

    checkpoint_repo: object
    heartbeat_repo: object
'''

_ASSEMBLY = '''"""What a caller of the boot assembly owns."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineAssemblyInputs:
    """The caller's half of the wiring."""

    provider: object
'''

_ENGINE = '''"""The engine."""


class AgentEngine:
    """Takes its wiring as one declared object."""

    def __init__(self, deps: EngineDependencies) -> None:
        self._deps = deps
'''

_ENGINE_REGROWN = '''"""The engine, back to keyword soup."""


class AgentEngine:
    """Takes whatever a caller remembered."""

    def __init__(self, provider=None, clock=None, memory_backend=None) -> None:
        self._provider = provider
'''

_SANCTIONED = '''"""The one place defaults may be supplied."""


def engine_deps(provider, **groups):
    return EngineDependencies(core=groups.get("core") or provider)
'''

_SANCTIONED_BUILDS_NOTHING = '''"""A declaration that outlived its module."""


def engine_deps(provider):
    return provider
'''


def _plant(root: Path, rel: str, source: str) -> None:
    """Write *source* at *rel* under the sandbox root."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _baseline(root: Path) -> None:
    """Plant a sandbox tree the gate should pass."""
    package = _MODULE._PACKAGE_REL
    _plant(root, f"{package}/__init__.py", _ROOT)
    _plant(root, f"{package}/_core.py", _BUNDLE)
    _plant(root, _MODULE._SATELLITE_TYPES["CheckpointWiring"], _WIRING)
    _plant(root, _MODULE._SATELLITE_TYPES["EngineAssemblyInputs"], _ASSEMBLY)
    _plant(root, _MODULE._ENGINE_REL, _ENGINE)
    _plant(root, _MODULE._SANCTIONED_REL, _SANCTIONED)


def _run(root: Path) -> int:
    """Run the gate against *root*.

    Returns:
        The gate's exit code.
    """
    return _MODULE.main(["--repo-root", str(root)])


class TestCleanTree:
    def test_a_total_declaration_passes(self, tmp_path: Path) -> None:
        _baseline(tmp_path)

        assert _run(tmp_path) == 0

    def test_the_sanctioned_module_may_supply_defaults(self, tmp_path: Path) -> None:
        """The one exemption is the whole point of naming it.

        ``engine_deps`` takes ``**groups`` and fills in every bundle a test
        did not name, which is the exact shape flagged anywhere else.
        """
        _baseline(tmp_path)

        assert _run(tmp_path) == 0


class TestDefaults:
    def test_a_default_on_a_bundle_field_fails(self, tmp_path: Path) -> None:
        _baseline(tmp_path)
        _plant(tmp_path, f"{_MODULE._PACKAGE_REL}/_core.py", _BUNDLE_WITH_DEFAULT)

        assert _run(tmp_path) == 1

    def test_a_default_on_a_satellite_field_fails(self, tmp_path: Path) -> None:
        _baseline(tmp_path)
        _plant(
            tmp_path,
            _MODULE._SATELLITE_TYPES["CheckpointWiring"],
            _WIRING.replace("heartbeat_repo: object", "heartbeat_repo: object = None"),
        )

        assert _run(tmp_path) == 1


class TestConstructions:
    @pytest.mark.parametrize(
        "call",
        [
            "EngineDependencies(**mapping)",
            "EngineCore(**mapping)",
            "CheckpointWiring(**mapping)",
            "EngineAssemblyInputs(**mapping)",
        ],
        ids=["root", "bundle", "wiring", "assembly_inputs"],
    )
    def test_a_splat_construction_fails(self, tmp_path: Path, call: str) -> None:
        _baseline(tmp_path)
        _plant(
            tmp_path,
            "src/synthorg/workers/runtime_builder.py",
            f'"""Boot."""\n\n\ndef build(mapping):\n    return {call}\n',
        )

        assert _run(tmp_path) == 1

    @pytest.mark.parametrize(
        "call",
        [
            "AgentEngine(deps=deps)",
            "AgentEngine(deps, extra)",
            "AgentEngine()",
            "AgentEngine(*args)",
        ],
        ids=["keyword", "two_positional", "none", "starred"],
    )
    def test_an_engine_call_that_is_not_one_object_fails(
        self, tmp_path: Path, call: str
    ) -> None:
        _baseline(tmp_path)
        _plant(
            tmp_path,
            "src/synthorg/workers/runtime_builder.py",
            f'"""Boot."""\n\n\ndef build(deps, extra, args):\n    return {call}\n',
        )

        assert _run(tmp_path) == 1

    def test_one_positional_object_is_the_shape_that_passes(
        self, tmp_path: Path
    ) -> None:
        _baseline(tmp_path)
        _plant(
            tmp_path,
            "src/synthorg/workers/runtime_builder.py",
            '"""Boot."""\n\n\ndef build(deps):\n    return AgentEngine(deps)\n',
        )

        assert _run(tmp_path) == 0


class TestBuilders:
    @pytest.mark.parametrize(
        "signature",
        [
            "def make(provider, core=None) -> EngineDependencies:",
            "def make(provider, *, core=None) -> EngineDependencies:",
            "def make(provider, **groups) -> EngineDependencies:",
        ],
        ids=["positional_default", "keyword_default", "kwargs"],
    )
    def test_a_defaults_supplying_builder_fails(
        self, tmp_path: Path, signature: str
    ) -> None:
        _baseline(tmp_path)
        _plant(
            tmp_path,
            "evals/harness/wiring.py",
            f'"""Harness."""\n\n\n{signature}\n    return provider\n',
        )

        assert _run(tmp_path) == 1

    def test_a_total_builder_is_not_one(self, tmp_path: Path) -> None:
        """Every parameter required means the caller still names everything."""
        _baseline(tmp_path)
        _plant(
            tmp_path,
            "evals/harness/wiring.py",
            '"""Harness."""\n\n\ndef make(core) -> EngineDependencies:\n'
            "    return EngineDependencies(core=core)\n",
        )

        assert _run(tmp_path) == 0


class TestBorrowedHelper:
    @pytest.mark.parametrize(
        "statement",
        [
            "from tests._shared import engine_deps",
            "import tests._shared.engine_deps",
            "from tests import _shared",
        ],
        ids=["from_import", "plain_import", "package_import"],
    )
    def test_the_harness_may_not_import_the_tests_package(
        self, tmp_path: Path, statement: str
    ) -> None:
        _baseline(tmp_path)
        _plant(tmp_path, "evals/harness/host.py", f'"""Harness."""\n\n{statement}\n')

        assert _run(tmp_path) == 1

    def test_a_test_module_may_import_it(self, tmp_path: Path) -> None:
        _baseline(tmp_path)
        _plant(
            tmp_path,
            "tests/unit/engine/test_engine.py",
            '"""Unit."""\n\nfrom tests._shared import engine_deps\n',
        )

        assert _run(tmp_path) == 0


class TestAnchors:
    """A scan that lost its anchor must not read as a clean scan."""

    def test_a_missing_package_is_a_configuration_error(self, tmp_path: Path) -> None:
        assert _run(tmp_path) == 2

    def test_a_missing_root_type_is_a_configuration_error(self, tmp_path: Path) -> None:
        _baseline(tmp_path)
        _plant(tmp_path, f"{_MODULE._PACKAGE_REL}/__init__.py", '"""Renamed away."""\n')

        assert _run(tmp_path) == 2

    def test_a_missing_satellite_is_a_configuration_error(self, tmp_path: Path) -> None:
        _baseline(tmp_path)
        (tmp_path / _MODULE._SATELLITE_TYPES["CheckpointWiring"]).unlink()

        assert _run(tmp_path) == 2

    def test_a_renamed_satellite_type_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        _baseline(tmp_path)
        _plant(
            tmp_path,
            _MODULE._SATELLITE_TYPES["CheckpointWiring"],
            _WIRING.replace("CheckpointWiring", "CheckpointBundle"),
        )

        assert _run(tmp_path) == 2

    def test_an_engine_back_to_keyword_soup_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        """The claim the gate rests on, so losing it is not a violation.

        A gate cannot report honestly on splats and builders once the one
        thing it is protecting has already been given away.
        """
        _baseline(tmp_path)
        _plant(tmp_path, _MODULE._ENGINE_REL, _ENGINE_REGROWN)

        assert _run(tmp_path) == 2

    def test_a_sanctioned_module_building_nothing_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        """An unused exemption is one the next builder inherits silently."""
        _baseline(tmp_path)
        _plant(tmp_path, _MODULE._SANCTIONED_REL, _SANCTIONED_BUILDS_NOTHING)

        assert _run(tmp_path) == 2
