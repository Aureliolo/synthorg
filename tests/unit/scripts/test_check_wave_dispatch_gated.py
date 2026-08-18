"""Unit tests for ``scripts/check_wave_dispatch_gated.py``.

The gate exists because the dependency rule was added to two of the three
wave loops the product ships. The third, ``ContextDependentDispatcher``, kept
its own copy of the loop and kept dispatching onto work that had already
failed; a live run showed it and the green unit suite did not, because every
test exercised a dispatcher that gated.

So the cases below are about the boundary the gate has to get right: what
counts as a wave loop at all. Reading a re-export as one would fail the
coordination barrel, which has no loop to gate.

Tests load the script via :mod:`importlib` and drive ``main`` against a fake
tree, matching ``test_check_enum_check_constraint_parity.py``.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_wave_dispatch_gated.py"
_PACKAGE_REL = "src/synthorg/engine/coordination"


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
            "_check_wave_dispatch_gated",
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

_GATE_OWNER = '''"""The module that defines the gate need not call it."""


def gate_wave() -> None:
    """Own the rule."""
'''


def _tree(tmp_path: Path, modules: dict[str, str]) -> Path:
    """Write a fake coordination package holding *modules*.

    Returns:
        The project root the gate should be pointed at.
    """
    package = tmp_path / _PACKAGE_REL
    package.mkdir(parents=True, exist_ok=True)
    (package / "_wave_parking.py").write_text(_GATE_OWNER, encoding="utf-8")
    for name, source in modules.items():
        (package / name).write_text(source, encoding="utf-8")
    return tmp_path


class TestWhatCountsAsAWaveLoop:
    """Calling the wave builder, not merely re-exporting it."""

    def test_a_loop_that_gates_passes(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            {
                "good.py": (
                    "from x import (\n"
                    "    abandon_after,\n"
                    "    abandon_stranded,\n"
                    "    build_execution_waves,\n"
                    "    gate_wave,\n"
                    ")\n"
                    "def dispatch():\n"
                    "    groups = build_execution_waves()\n"
                    "    runnable = [gate_wave(g) for g in groups]\n"
                    "    abandon_stranded(groups[0], 0)\n"
                    "    abandon_after(groups, 0)\n"
                    "    return runnable\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 0

    def test_a_loop_that_does_not_gate_is_reported(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            {
                "bad.py": (
                    "from x import build_execution_waves\n"
                    "def dispatch():\n"
                    "    return build_execution_waves()\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 1

    def test_a_loop_that_gates_but_never_abandons_is_reported(
        self, tmp_path: Path
    ) -> None:
        """Gating covers the wave dispatched, not the ones never reached.

        A live run stopped after its first wave failed and left two
        subtasks of a later wave at CREATED: no dispatcher would run them,
        no gate would park them, and the rollup needs every item terminal,
        so the plan read ``executing`` for ever.
        """
        root = _tree(
            tmp_path,
            {
                "half.py": (
                    "from x import build_execution_waves, gate_wave\n"
                    "def dispatch():\n"
                    "    groups = build_execution_waves()\n"
                    "    return [gate_wave(g) for g in groups]\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 1

    def test_a_loop_that_never_parks_a_failed_waves_own_rows_is_reported(
        self, tmp_path: Path
    ) -> None:
        """A wave that RAN owns its outcome; one that RAISED does not.

        ``abandon_after`` skips the wave the run stopped at, which is right
        for a wave that ran and wrong for one whose assignment raised
        partway: the rows it never dispatched stay at CREATED, which is not
        a non-delivering status, so the next wave's gate reads them as still
        on their way and dispatches against outputs nobody will write.
        """
        root = _tree(
            tmp_path,
            {
                "two_thirds.py": (
                    "from x import abandon_after, build_execution_waves, gate_wave\n"
                    "def dispatch():\n"
                    "    groups = build_execution_waves()\n"
                    "    abandon_after(groups, 0)\n"
                    "    return [gate_wave(g) for g in groups]\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 1

    def test_reaching_the_gate_through_the_shared_runner_passes(
        self, tmp_path: Path
    ) -> None:
        """``execute_waves`` gates for its callers, so calling it is enough."""
        root = _tree(
            tmp_path,
            {
                "shared.py": (
                    "from x import build_execution_waves, execute_waves\n"
                    "def dispatch():\n"
                    "    return execute_waves(build_execution_waves())\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 0

    def test_gating_from_an_extracted_sibling_passes(self, tmp_path: Path) -> None:
        """The guard is the reachable call, not the file it sits in.

        Moving a loop's parking into a sibling module is the ordinary way a
        module stays under its size cap, and a name-only reading calls that
        extraction a dispatcher that stopped gating. The gate would then fail
        correct code and teach that the fix is to inline the helper back.
        """
        root = _tree(
            tmp_path,
            {
                "dispatcher.py": (
                    "from x import build_execution_waves\n"
                    "from .parking import park_everything\n"
                    "def dispatch():\n"
                    "    groups = build_execution_waves()\n"
                    "    return park_everything(groups)\n"
                ),
                "parking.py": (
                    "from x import abandon_after, abandon_stranded, gate_wave\n"
                    "def park_everything(groups):\n"
                    "    runnable = [gate_wave(g) for g in groups]\n"
                    "    abandon_stranded(groups[0], 0)\n"
                    "    abandon_after(groups, 0)\n"
                    "    return runnable\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 0

    def test_gating_through_a_bare_relative_import_passes(self, tmp_path: Path) -> None:
        """``from . import parking`` reaches a sibling like any other form.

        This spelling leaves ``ImportFrom.module`` empty and puts the sibling
        in the alias list, so a reading that consults ``module`` alone sees no
        import here at all and the dispatcher reads as one that never gates.
        Which import style a module happens to use cannot decide whether the
        rule applies to it.
        """
        root = _tree(
            tmp_path,
            {
                "dispatcher.py": (
                    "from x import build_execution_waves\n"
                    "from . import parking\n"
                    "def dispatch():\n"
                    "    groups = build_execution_waves()\n"
                    "    return parking.park_everything(groups)\n"
                ),
                "parking.py": (
                    "from x import abandon_after, abandon_stranded, gate_wave\n"
                    "def park_everything(groups):\n"
                    "    runnable = [gate_wave(g) for g in groups]\n"
                    "    abandon_stranded(groups[0], 0)\n"
                    "    abandon_after(groups, 0)\n"
                    "    return runnable\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 0

    def test_a_parent_package_import_is_not_a_sibling(self, tmp_path: Path) -> None:
        """``from .. import x`` leaves the package, so it credits nothing.

        Both spellings leave ``ImportFrom.module`` empty, so a check that
        only asks whether the import is relative reads a parent-package
        import as a sibling. If the package then happens to hold a module by
        that name, the real sibling's gate calls are credited to a dispatcher
        that never reaches them, and the gate passes an ungated wave loop:
        the one verdict it exists to prevent.

        The fake tree gives the parent import the same name as the gating
        sibling, which is what makes the mistake observable rather than
        merely possible.
        """
        root = _tree(
            tmp_path,
            {
                "dispatcher.py": (
                    "from x import build_execution_waves\n"
                    "from .. import parking\n"
                    "def dispatch():\n"
                    "    groups = build_execution_waves()\n"
                    "    return parking.park_everything(groups)\n"
                ),
                "parking.py": (
                    "from x import abandon_after, abandon_stranded, gate_wave\n"
                    "def park_everything(groups):\n"
                    "    runnable = [gate_wave(g) for g in groups]\n"
                    "    abandon_stranded(groups[0], 0)\n"
                    "    abandon_after(groups, 0)\n"
                    "    return runnable\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 1

    def test_a_sibling_that_gates_nothing_still_reports(self, tmp_path: Path) -> None:
        """Following imports must not become a way to pass by association.

        The walk widens what counts as reaching the gate, so the case that
        matters is the one it must still refuse: a dispatcher importing a
        sibling that does not gate either.
        """
        root = _tree(
            tmp_path,
            {
                "dispatcher.py": (
                    "from x import build_execution_waves\n"
                    "from .helpers import tidy\n"
                    "def dispatch():\n"
                    "    return tidy(build_execution_waves())\n"
                ),
                "helpers.py": ("def tidy(groups):\n    return groups\n"),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 1

    def test_a_re_export_is_not_a_wave_loop(self, tmp_path: Path) -> None:
        """The false positive the first version produced.

        The package barrel re-exports the builder for consumers and
        dispatches nothing, so it has no wave to gate.
        """
        root = _tree(
            tmp_path,
            {
                "__init__.py": (
                    "from x import build_execution_waves\n"
                    '__all__ = ["build_execution_waves"]\n'
                ),
                "good.py": (
                    "from x import (\n"
                    "    abandon_after,\n"
                    "    abandon_stranded,\n"
                    "    build_execution_waves,\n"
                    "    gate_wave,\n"
                    ")\n"
                    "def dispatch():\n"
                    "    abandon_after(0)\n"
                    "    abandon_stranded(0)\n"
                    "    return gate_wave(build_execution_waves())\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 0

    def test_an_aliased_builder_still_counts(self, tmp_path: Path) -> None:
        """An import style cannot decide whether the rule applies."""
        root = _tree(
            tmp_path,
            {
                "aliased.py": (
                    "from x import build_execution_waves as build\n"
                    "def dispatch():\n"
                    "    return build()\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 1

    def test_a_qualified_call_still_counts(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            {
                "qualified.py": (
                    "from synthorg.engine.coordination import group_builder\n"
                    "def dispatch():\n"
                    "    return group_builder.build_execution_waves()\n"
                ),
            },
        )
        assert _MODULE.main(["--repo-root", str(root)]) == 1


class TestFailClosed:
    def test_no_wave_loop_at_all_is_a_configuration_error(self, tmp_path: Path) -> None:
        """A renamed builder must not read as a clean tree.

        With nothing matching, the gate is looking at nothing and saying so
        beats reporting success.
        """
        root = _tree(tmp_path, {"unrelated.py": "def helper() -> None:\n    pass\n"})
        assert _MODULE.main(["--repo-root", str(root)]) == 2

    def test_a_missing_repo_root_is_a_configuration_error(self, tmp_path: Path) -> None:
        assert _MODULE.main(["--repo-root", str(tmp_path / "absent")]) == 2


class TestRealTree:
    """Every dispatcher the product ships gates its waves."""

    def test_live_tree_is_clean(self) -> None:
        assert _MODULE.main(["--repo-root", str(_REPO_ROOT)]) == 0
