"""Unit tests for ``scripts/check_skeleton_stage_paths.py``.

Two invariants, and for each the ways it can be hollowed out: an edge that steps
over the contract stage (including one added several statuses away, which is why
the walk is a graph search rather than a check on the one edge that used to
exist), and a manifest gate declared with nothing to require evidence of it.

The tree is written into ``tmp_path`` and the gate is pointed at it, so no test
reads the live repository.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_skeleton_stage_paths.py"


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _TRANSITIONS_REL: str
    _MANIFEST_REL: str

    @staticmethod
    def scan_repo(repo_root: Path) -> tuple[str, ...]:
        """Check both properties over *repo_root*."""
        ...


def _load() -> _ScriptModule:
    """Import the gate script as a module.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "check_skeleton_stage_paths", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("_ScriptModule", module)


_GATE = _load()

_HEALTHY_TRANSITIONS = """\
PLAN_TRANSITIONS = {
    PlanStatus.APPROVED: frozenset(
        {PlanStatus.SKELETON, PlanStatus.SUPERSEDED, PlanStatus.FAILED}
    ),
    PlanStatus.SKELETON: frozenset(
        {PlanStatus.EXECUTING, PlanStatus.SUPERSEDED, PlanStatus.FAILED}
    ),
    PlanStatus.EXECUTING: frozenset({PlanStatus.INTEGRATING, PlanStatus.FAILED}),
}
"""

_HEALTHY_MANIFEST = """\
class EnvironmentManifest(BaseModel):
    language: NotBlankStr
    test_command: NotBlankStr
    lint_command: NotBlankStr | None = None
    format_command: NotBlankStr | None = None
    dependency_check_command: NotBlankStr | None = None

    @property
    def declared_gates(self) -> Mapping[CodeExecutionPurpose, str]:
        declared = {
            CodeExecutionPurpose.LINT: self.lint_command,
            CodeExecutionPurpose.FORMAT: self.format_command,
            CodeExecutionPurpose.DEPENDENCY: self.dependency_check_command,
        }
        return {p: str(c) for p, c in declared.items() if c is not None}
"""


def _tree(
    tmp_path: Path,
    *,
    transitions: str = _HEALTHY_TRANSITIONS,
    manifest: str = _HEALTHY_MANIFEST,
) -> Path:
    """Write a minimal tree the gate can scan.

    Returns:
        The repo root to point the gate at.
    """
    for rel, body in (
        (_GATE._TRANSITIONS_REL, transitions),
        (_GATE._MANIFEST_REL, manifest),
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


class TestAHealthyTree:
    def test_passes(self, tmp_path: Path) -> None:
        assert _GATE.scan_repo(_tree(tmp_path)) == ()


class TestTheContractStageMustBeUnskippable:
    def test_a_direct_edge_past_it_is_refused(self, tmp_path: Path) -> None:
        """The shape the stage replaced: approval dispatched the units itself."""
        tree = _tree(
            tmp_path,
            transitions=_HEALTHY_TRANSITIONS.replace(
                "{PlanStatus.SKELETON, PlanStatus.SUPERSEDED",
                "{PlanStatus.SKELETON, PlanStatus.EXECUTING, PlanStatus.SUPERSEDED",
            ),
        )

        violations = _GATE.scan_repo(tree)

        assert len(violations) == 1
        assert "reaches PlanStatus.EXECUTING directly" in violations[0]

    def test_a_route_around_it_several_statuses_long_is_refused(
        self, tmp_path: Path
    ) -> None:
        """A literal check on the one edge would pass this and re-open the hole.

        Nothing here names APPROVED and EXECUTING in the same entry: the path
        runs through a status added later, which is exactly how the invariant
        rots once the obvious edge is gone.
        """
        transitions = """\
PLAN_TRANSITIONS = {
    PlanStatus.APPROVED: frozenset({PlanStatus.SKELETON, PlanStatus.STAGING}),
    PlanStatus.STAGING: frozenset({PlanStatus.PREFLIGHT}),
    PlanStatus.PREFLIGHT: frozenset({PlanStatus.EXECUTING}),
    PlanStatus.SKELETON: frozenset({PlanStatus.EXECUTING}),
    PlanStatus.EXECUTING: frozenset({PlanStatus.FAILED}),
}
"""

        violations = _GATE.scan_repo(_tree(tmp_path, transitions=transitions))

        assert len(violations) == 1
        assert "without passing through PlanStatus.SKELETON" in violations[0]

    def test_a_stage_with_no_way_out_is_refused(self, tmp_path: Path) -> None:
        """Unskippable and unpassable is a plan parked for ever."""
        transitions = """\
PLAN_TRANSITIONS = {
    PlanStatus.APPROVED: frozenset({PlanStatus.SKELETON}),
    PlanStatus.SKELETON: frozenset({PlanStatus.FAILED}),
    PlanStatus.EXECUTING: frozenset({PlanStatus.FAILED}),
}
"""

        violations = _GATE.scan_repo(_tree(tmp_path, transitions=transitions))

        assert len(violations) == 1
        assert "cannot reach PlanStatus.EXECUTING" in violations[0]

    def test_a_table_that_stopped_declaring_the_stage_is_a_config_error(
        self, tmp_path: Path
    ) -> None:
        """Not a violation: the gate has gone blind and must say so.

        Reported as a config error rather than a pass, because a table with no
        SKELETON entry reads exactly like a tree with nothing to find.
        """
        transitions = """\
PLAN_TRANSITIONS = {
    PlanStatus.APPROVED: frozenset({PlanStatus.EXECUTING}),
    PlanStatus.EXECUTING: frozenset({PlanStatus.FAILED}),
}
"""

        with pytest.raises(ValueError, match=r"declares no PlanStatus\.SKELETON"):
            _GATE.scan_repo(_tree(tmp_path, transitions=transitions))


class TestEveryDeclaredGateMustBeRead:
    def test_a_command_field_no_gate_map_reads_is_refused(self, tmp_path: Path) -> None:
        """The regression this rule exists for: a knob an operator sets in vain."""
        manifest = _HEALTHY_MANIFEST.replace(
            "    dependency_check_command: NotBlankStr | None = None\n",
            "    dependency_check_command: NotBlankStr | None = None\n"
            "    audit_command: NotBlankStr | None = None\n",
        )

        violations = _GATE.scan_repo(_tree(tmp_path, manifest=manifest))

        assert len(violations) == 1
        assert "EnvironmentManifest.audit_command is declared" in violations[0]

    def test_the_test_command_is_not_a_gate(self, tmp_path: Path) -> None:
        """It produces the evidence the others are judged beside.

        The oracle reads its records directly, so requiring it in the derived
        map would flag the one command field that is definitionally read.
        """
        assert _GATE.scan_repo(_tree(tmp_path)) == ()

    def test_a_manifest_that_stopped_deriving_gates_is_a_config_error(
        self, tmp_path: Path
    ) -> None:
        """Losing the derivation reads as "no gates declared", which passes.

        That is the silent-blindness failure, so it is exit 2 rather than a
        clean run over a manifest nothing enforces any more.
        """
        manifest = _HEALTHY_MANIFEST[: _HEALTHY_MANIFEST.index("    @property")]

        with pytest.raises(ValueError, match=r"declares no declared_gates"):
            _GATE.scan_repo(_tree(tmp_path, manifest=manifest))
