# module-kind: tests
"""The liveness probe: a third verdict, asked apart from the score.

Two things decide whether it is honest: what counts as dead, and that the
program it runs can never see the oracle. Both are asserted on doubles rather
than left to a real container.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from evals.errors import OracleUnusableError
from evals.recursion_depth.grading import ORACLE_SUITE_DIR, ORACLE_TREE_DIR
from evals.recursion_depth.liveness import (
    EntryPoint,
    LivenessDeclaration,
    LivenessOutcome,
    classify_entry,
    classify_import,
    declared_liveness,
    entry_argv,
    import_argv,
    probe_liveness,
)
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import CellRecord, Liveness
from synthorg.tools.sandbox import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_SPEC_DIR = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "recursion_depth"
    / "spec"
    / "sqlcsv"
)

_TRACEBACK = 'Traceback (most recent call last):\n  File "x", line 1\nValueError\n'

_BLOCK = "liveness:\n  modules: [sqlcsv]\n  entry_points:\n    - module: sqlcsv\n"


def _result(
    *, returncode: int = 0, stderr: str = "", timed_out: bool = False
) -> SandboxResult:
    """One probe's captured run.

    Returns:
        The result.
    """
    return SandboxResult(
        stdout="", stderr=stderr, returncode=returncode, timed_out=timed_out
    )


class TestWhatTheSpecDeclares:
    """Absent is a fact, malformed is a refusal, empty is malformed."""

    def test_the_committed_spec_declares_its_deliverable(self) -> None:
        index = {"liveness": {"modules": ["sqlcsv"], "entry_points": []}}

        declaration = declared_liveness(index, spec_dir=_SPEC_DIR)

        assert declaration is not None
        assert declaration.modules == ("sqlcsv",)

    def test_a_spec_with_no_block_declares_none(self) -> None:
        assert declared_liveness({"spec_id": "x"}, spec_dir=_SPEC_DIR) is None

    def test_an_unreadable_block_stops_the_matrix(self) -> None:
        with pytest.raises(OracleUnusableError, match="cannot read"):
            declared_liveness({"liveness": {"modulez": ["x"]}}, spec_dir=_SPEC_DIR)

    def test_an_empty_block_is_refused_rather_than_read_as_live(self) -> None:
        with pytest.raises(ValueError, match="at least one module"):
            LivenessDeclaration()

    def test_a_name_that_is_not_a_module_is_refused(self) -> None:
        # The name is interpolated into `-c` source, so anything else would
        # either fail to parse or run as code.
        with pytest.raises(ValueError, match="dotted module name"):
            EntryPoint(module="sqlcsv; import os")


class TestTheProbeArgv:
    """Isolated, rooted in the staged tree, and POSIX on every host."""

    def test_an_import_probe_is_isolated_and_finds_the_tree(self) -> None:
        argv = import_argv("sqlcsv")

        assert argv[0] == "-I"
        assert ORACLE_TREE_DIR in argv[-1]
        assert "import sqlcsv" in argv[-1]
        assert "\\" not in argv[-1]

    def test_an_entry_probe_runs_the_module_as_main_with_its_args(self) -> None:
        argv = entry_argv(EntryPoint(module="sqlcsv", args=("--help",)))

        assert argv[0] == "-I"
        assert "run_name='__main__'" in argv[-1]
        assert "['sqlcsv', '--help']" in argv[-1]


class TestWhatCountsAsDead:
    """A traceback or a hang, never an exit status the program chose."""

    def test_a_usage_error_is_alive(self) -> None:
        outcome = classify_entry(
            _result(returncode=2, stderr="usage: sqlcsv [-h]\n"),
            entry=EntryPoint(module="sqlcsv"),
        )

        assert outcome.verdict is Liveness.LIVE
        assert outcome.detail == ""

    def test_an_uncaught_exception_is_dead(self) -> None:
        outcome = classify_entry(
            _result(returncode=1, stderr=_TRACEBACK),
            entry=EntryPoint(module="sqlcsv", args=("--help",)),
        )

        assert outcome.verdict is Liveness.DEAD
        assert "sqlcsv --help raised" in outcome.detail

    def test_a_hang_is_dead(self) -> None:
        outcome = classify_entry(
            _result(timed_out=True), entry=EntryPoint(module="sqlcsv")
        )

        assert outcome.verdict is Liveness.DEAD
        assert "did not finish" in outcome.detail

    @pytest.mark.parametrize("returncode", [-11, 137, 139])
    def test_a_program_a_signal_ended_is_dead(self, returncode: int) -> None:
        # A segfault or the container's memory limit writes no traceback, so
        # the marker alone would read the death as an exit the program chose.
        outcome = classify_entry(
            _result(returncode=returncode, stderr=""),
            entry=EntryPoint(module="sqlcsv"),
        )

        assert outcome.verdict is Liveness.DEAD
        assert "ended by a signal" in outcome.detail

    def test_a_module_that_does_not_import_is_dead(self) -> None:
        """An import has no exit of its own to choose, so non-zero is dead."""
        failed = _result(returncode=1, stderr="ModuleNotFoundError")

        outcome = classify_import(failed, module="sqlcsv")

        assert outcome.verdict is Liveness.DEAD
        assert "importing sqlcsv failed" in outcome.detail

    def test_a_module_that_imports_is_alive(self) -> None:
        assert classify_import(_result(), module="sqlcsv").verdict is Liveness.LIVE


def _spec(tmp_path: Path, block: str) -> Path:
    """A spec directory whose index carries *block*.

    Returns:
        The directory.
    """
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "requirements.yaml").write_text(
        f"spec_id: probe\noracle_dir: oracle\nrequirements: []\n{block}",
        encoding="utf-8",
    )
    return spec_dir


def _tree(tmp_path: Path) -> Path:
    """A produced tree with one module in it.

    Returns:
        The tree.
    """
    tree = tmp_path / "tree"
    (tree / "sqlcsv").mkdir(parents=True)
    (tree / "sqlcsv" / "__init__.py").write_text("", encoding="utf-8")
    return tree


class TestTheProbeRun:
    """Its own container, holding the tree alone, released when done."""

    async def test_a_spec_declaring_nothing_builds_no_container(
        self, tmp_path: Path
    ) -> None:
        def never(_root: Path, /, *, owner: str) -> SandboxBackend:
            msg = f"no container should be built for {owner}"
            raise AssertionError(msg)

        outcome = await probe_liveness(
            build_sandbox=never, spec_dir=_spec(tmp_path, ""), tree=_tree(tmp_path)
        )

        assert outcome.verdict is Liveness.NOT_PROBEABLE
        assert "declares no module" in outcome.detail

    async def test_the_probe_stages_the_tree_and_nothing_else(
        self, tmp_path: Path
    ) -> None:
        """The program runs where no expectation exists to be read."""
        staged: list[list[str]] = []
        execute = AsyncMock(spec=SandboxBackend.execute, return_value=_result())
        backend: SandboxBackend = mock_of[SandboxBackend](execute=execute)

        def factory(root: Path, /, *, owner: str) -> SandboxBackend:
            assert owner.startswith("liveness:")
            staged.append(sorted(path.name for path in root.iterdir()))
            return backend

        outcome = await probe_liveness(
            build_sandbox=factory,
            spec_dir=_spec(tmp_path, _BLOCK),
            tree=_tree(tmp_path),
        )

        assert outcome.verdict is Liveness.LIVE
        assert staged == [[ORACLE_TREE_DIR]]
        assert ORACLE_SUITE_DIR not in staged[0]
        assert execute.await_count == 2

    async def test_a_dead_module_stops_before_its_entry_point_runs(
        self, tmp_path: Path
    ) -> None:
        execute = AsyncMock(
            spec=SandboxBackend.execute,
            return_value=_result(returncode=1, stderr="ModuleNotFoundError"),
        )
        backend: SandboxBackend = mock_of[SandboxBackend](execute=execute)
        released: list[str] = []

        async def release(owner: str) -> None:
            released.append(owner)

        outcome = await probe_liveness(
            build_sandbox=lambda _root, *, owner: backend,
            release_sandboxes=release,
            spec_dir=_spec(tmp_path, _BLOCK),
            tree=_tree(tmp_path),
        )

        assert outcome.verdict is Liveness.DEAD
        assert execute.await_count == 1
        assert len(released) == 1


class TestTheVerdictOnTheRecord:
    """A third state, and one that cannot contradict itself."""

    def _measured(self, **fields: object) -> CellRecord:
        base: dict[str, object] = {
            "depth_cap": 1,
            "arm": Arm.GATED,
            "repetition": 0,
            "achieved_depth": 1,
        }
        return CellRecord.model_validate({**base, **fields})

    def test_a_recording_that_never_asked_reads_as_none(self) -> None:
        assert self._measured().liveness is None

    def test_a_dead_verdict_carries_what_died(self) -> None:
        record = self._measured(liveness=Liveness.DEAD, liveness_detail="raised")

        assert record.liveness is Liveness.DEAD
        assert record.liveness_detail == "raised"

    def test_a_live_verdict_carrying_a_death_is_refused(self) -> None:
        with pytest.raises(ValueError, match="reads live and still says"):
            self._measured(liveness=Liveness.LIVE, liveness_detail="raised")

    def test_an_unavailable_cell_carries_no_verdict(self) -> None:
        with pytest.raises(ValueError, match="never graded"):
            CellRecord(
                depth_cap=1,
                arm=Arm.GATED,
                repetition=0,
                unavailable_reason="gone",
                liveness=Liveness.LIVE,
            )

    @pytest.mark.parametrize("verdict", [Liveness.DEAD, Liveness.NOT_PROBEABLE])
    def test_a_verdict_that_is_not_live_names_its_reason(
        self, verdict: Liveness
    ) -> None:
        with pytest.raises(ValueError, match="names no reason"):
            self._measured(liveness=verdict, liveness_detail="")


class TestTheOutcomeAgreesWithItself:
    """The probe's own value refuses the disagreement before the record does."""

    def test_a_live_outcome_carrying_a_death_is_refused(self) -> None:
        with pytest.raises(ValueError, match="live outcome still says"):
            LivenessOutcome(Liveness.LIVE, "raised")

    @pytest.mark.parametrize("verdict", [Liveness.DEAD, Liveness.NOT_PROBEABLE])
    def test_an_outcome_that_is_not_live_names_its_reason(
        self, verdict: Liveness
    ) -> None:
        with pytest.raises(ValueError, match="names no reason"):
            LivenessOutcome(verdict)
