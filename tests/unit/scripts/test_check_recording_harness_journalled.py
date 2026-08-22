# module-kind: tests
"""The gate catches a recording that can lose paid cells."""

from pathlib import Path

import pytest
from scripts.check_recording_harness_journalled import main

pytestmark = pytest.mark.unit

_SHARED = '''
"""The shared writer."""


def open_journal(out_dir, spec, *, identity, resume):
    """Open it."""
    return None, None
'''

_BINDING = '''
"""A harness binding."""

from evals.harness.journal import open_journal


def open_cell_journal(out_dir, *, provenance, resume):
    """Bind it."""
    return open_journal(out_dir, None, identity={}, resume=resume)
'''

_HAND_ROLLED_BINDING = '''
"""A binding that grew its own file handling."""


def open_cell_journal(out_dir, *, provenance, resume):
    """Do it here instead."""
    return (out_dir / "cells.jsonl").open("a"), None
'''

_JOURNALLING_DRIVER = '''
"""A driver that journals as it goes."""

from evals.recursion_depth.journal import open_cell_journal
from evals.recursion_depth.models import RecursionDepthReport


async def run_sweep(context, *, provenance, out_dir, resume):
    """Run it."""
    records, _resumed = open_cell_journal(out_dir, provenance=provenance, resume=resume)
    return RecursionDepthReport(cells=records.cells)
'''

_ACCUMULATING_DRIVER = '''
"""A driver that holds everything until the end."""

from evals.recursion_depth.models import RecursionDepthReport


async def run_sweep(context, *, provenance):
    """Run it."""
    cells = []
    return RecursionDepthReport(cells=tuple(cells))
'''

_DEAD_OPEN_DRIVER = '''
"""A driver whose journal open sits where the matrix never goes."""

from evals.recursion_depth.journal import open_cell_journal
from evals.recursion_depth.models import RecursionDepthReport


def _unused_someday(out_dir, provenance, resume):
    """Nothing on the recording path calls this."""
    return open_cell_journal(out_dir, provenance=provenance, resume=resume)


async def run_sweep(context, *, provenance, out_dir, resume):
    """Run it, journalling nothing."""
    cells = []
    return RecursionDepthReport(cells=tuple(cells))
'''

_DEAD_OPEN_BINDING = '''
"""A binding whose shared call is not on its entry point."""

from evals.harness.journal import open_journal


def _never_called(out_dir):
    """Nothing reaches this."""
    return open_journal(out_dir, None, identity={}, resume=False)


def open_cell_journal(out_dir, *, provenance, resume):
    """Bind it, badly."""
    return (out_dir / "cells.jsonl").open("a"), None
'''

_HELPER = '''
"""A runner-adjacent module that ends no matrix."""


def summarise(rows):
    """Add them up."""
    return sum(rows)
'''


def _harness(root: Path, name: str, *, driver: str, binding: str) -> None:
    """Write one harness package under *root*.

    Args:
        root: The repository root being built.
        name: The harness package name.
        driver: Source for its ``runner.py``.
        binding: Source for its ``journal.py``.
    """
    package = root / "evals" / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "runner.py").write_text(driver, encoding="utf-8")
    (package / "journal.py").write_text(binding, encoding="utf-8")


def _tree(root: Path) -> None:
    """Write the shared writer every harness binds.

    Args:
        root: The repository root being built.
    """
    shared = root / "evals" / "harness"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "journal.py").write_text(_SHARED, encoding="utf-8")


class TestTheGatePasses:
    """A harness that journals is what the rule asks for."""

    def test_a_journalling_harness_passes(self, tmp_path: Path) -> None:
        _tree(tmp_path)
        _harness(tmp_path, "sweep", driver=_JOURNALLING_DRIVER, binding=_BINDING)

        assert main(["--repo-root", str(tmp_path)]) == 0

    def test_a_module_that_ends_no_matrix_is_not_a_driver(self, tmp_path: Path) -> None:
        # The population is "assembles a report", not "is called runner.py":
        # a helper that happens to sit at that path records nothing.
        _tree(tmp_path)
        _harness(tmp_path, "sweep", driver=_JOURNALLING_DRIVER, binding=_BINDING)
        (tmp_path / "evals" / "other").mkdir(parents=True)
        (tmp_path / "evals" / "other" / "runner.py").write_text(
            _HELPER, encoding="utf-8"
        )

        assert main(["--repo-root", str(tmp_path)]) == 0


class TestTheGateCatchesTheDefect:
    """The two shapes that lost real money."""

    def test_a_driver_that_only_accumulates_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Seven hours, one cell measured, nothing on disk.
        _tree(tmp_path)
        _harness(tmp_path, "sweep", driver=_ACCUMULATING_DRIVER, binding=_BINDING)

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "opens no journal" in capsys.readouterr().out

    def test_a_binding_that_hand_rolls_durability_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A second copy of the fsync-and-header logic is how one harness comes
        # to be quietly less crash-safe than its sibling.
        _tree(tmp_path)
        _harness(
            tmp_path,
            "sweep",
            driver=_JOURNALLING_DRIVER,
            binding=_HAND_ROLLED_BINDING,
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "second copy" in capsys.readouterr().out


class TestAnOpenCallIsNotEnoughOnItsOwn:
    """The call has to be where the matrix actually runs."""

    def test_a_driver_whose_open_sits_in_dead_code_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A module-wide union of called names passes this, and it journals
        # exactly nothing.
        _tree(tmp_path)
        _harness(tmp_path, "sweep", driver=_DEAD_OPEN_DRIVER, binding=_BINDING)

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "opens no journal" in capsys.readouterr().out

    def test_a_binding_whose_shared_call_is_off_the_entry_point_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _tree(tmp_path)
        _harness(
            tmp_path,
            "sweep",
            driver=_JOURNALLING_DRIVER,
            binding=_DEAD_OPEN_BINDING,
        )

        assert main(["--repo-root", str(tmp_path)]) == 1
        assert "entry point" in capsys.readouterr().out


class TestTheGateFailsClosed:
    """Finding nothing is a configuration error, never a pass."""

    def test_no_driver_at_all_is_a_configuration_error(self, tmp_path: Path) -> None:
        _tree(tmp_path)

        assert main(["--repo-root", str(tmp_path)]) == 2

    def test_an_unresolvable_root_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        assert main(["--repo-root", str(tmp_path / "absent")]) == 2
