"""Unit tests for ``scripts/check_stall_reason_maps_total.py``.

Three properties, and for each the way it goes quiet: a reason with no replan
guidance, a reason no family re-confirms, a reason both families claim, and a
declaration the gate can no longer find, which is the one that must fail loudly
rather than read as an enum with nothing missing.

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
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_stall_reason_maps_total.py"


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    _COMPLETION_REL: str
    _BRIEF_REL: str

    @staticmethod
    def scan_repo(repo_root: Path) -> tuple[str, ...]:
        """Check every stall reason is answered over *repo_root*."""
        ...


def _load() -> _ScriptModule:
    """Import the gate script as a module.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "check_stall_reason_maps_total", _SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return cast("_ScriptModule", module)


_GATE = _load()

_HEALTHY_COMPLETION = """\
class StallReason(StrEnum):
    ALL_FAILED = "all_failed"
    SKELETON_FAILED = "skeleton_failed"
    EVALUATION_UNMET = "evaluation_unmet"


ITEM_DERIVED_STALLS: Final[frozenset[StallReason]] = frozenset(
    {StallReason.ALL_FAILED}
)

STAGE_OF_STALL_REASON: Final[Mapping[StallReason, PlanStatus]] = MappingProxyType(
    {
        StallReason.SKELETON_FAILED: PlanStatus.SKELETON,
        StallReason.EVALUATION_UNMET: PlanStatus.EVALUATING,
    }
)
"""

_HEALTHY_BRIEF = """\
_REASON_GUIDANCE: Final[dict[StallReason, str]] = {
    StallReason.ALL_FAILED: "change the approach",
    StallReason.SKELETON_FAILED: "re-shape the objective",
    StallReason.EVALUATION_UNMET: "close the gap against the criteria",
}
"""


def _tree(
    tmp_path: Path,
    *,
    completion: str = _HEALTHY_COMPLETION,
    brief: str = _HEALTHY_BRIEF,
) -> Path:
    """Write a minimal tree the gate can scan.

    Returns:
        The repo root to point the gate at.
    """
    for rel, body in (
        (_GATE._COMPLETION_REL, completion),
        (_GATE._BRIEF_REL, brief),
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


class TestAHealthyTree:
    def test_passes(self, tmp_path: Path) -> None:
        assert _GATE.scan_repo(_tree(tmp_path)) == ()


class TestEveryReasonMustBeAnswered:
    def test_a_reason_with_no_guidance_is_refused(self, tmp_path: Path) -> None:
        """The replan brief indexes this map directly, so a gap raises."""
        brief = _HEALTHY_BRIEF.replace(
            '    StallReason.SKELETON_FAILED: "re-shape the objective",\n', ""
        )

        violations = _GATE.scan_repo(_tree(tmp_path, brief=brief))

        assert len(violations) == 1
        assert "has no _REASON_GUIDANCE entry" in violations[0]

    def test_a_reason_in_neither_family_is_refused(self, tmp_path: Path) -> None:
        """The shipped defect: fired by the rollup, re-confirmed by nothing.

        Two consumers index the stage map with ``[]``, so the initiative never
        replans and never escalates while the sweep re-drives it for ever.
        """
        completion = _HEALTHY_COMPLETION.replace(
            "        StallReason.SKELETON_FAILED: PlanStatus.SKELETON,\n", ""
        )

        violations = _GATE.scan_repo(_tree(tmp_path, completion=completion))

        assert len(violations) == 1
        assert (
            "is in neither ITEM_DERIVED_STALLS nor STAGE_OF_STALL_REASON"
            in (violations[0])
        )

    def test_a_reason_in_both_families_is_refused(self, tmp_path: Path) -> None:
        """Two answers to how one stall is re-confirmed is not a redundancy."""
        completion = _HEALTHY_COMPLETION.replace(
            "    {StallReason.ALL_FAILED}",
            "    {StallReason.ALL_FAILED, StallReason.EVALUATION_UNMET}",
        )

        violations = _GATE.scan_repo(_tree(tmp_path, completion=completion))

        assert len(violations) == 1
        assert (
            "is in both ITEM_DERIVED_STALLS and STAGE_OF_STALL_REASON"
            in (violations[0])
        )

    def test_an_entry_for_a_retired_reason_is_refused(self, tmp_path: Path) -> None:
        """An allowance that outlives its member is one the next member inherits."""
        brief = _HEALTHY_BRIEF.replace(
            "}", '    StallReason.RETIRED: "answers nothing",\n}'
        )

        violations = _GATE.scan_repo(_tree(tmp_path, brief=brief))

        assert len(violations) == 1
        assert "is not a member of the enum" in violations[0]


class TestLosingADeclarationIsAConfigError:
    @pytest.mark.parametrize(
        ("removed", "expected"),
        [
            pytest.param("ITEM_DERIVED_STALLS", "ITEM_DERIVED_STALLS", id="family"),
            pytest.param(
                "STAGE_OF_STALL_REASON", "STAGE_OF_STALL_REASON", id="stage_map"
            ),
        ],
    )
    def test_a_missing_declaration_raises(
        self, tmp_path: Path, removed: str, expected: str
    ) -> None:
        """A declaration nothing can find reads exactly like nothing missing.

        That is the silent-blindness failure, so it is exit 2 rather than a
        clean run over maps nothing holds together any more.
        """
        completion = _HEALTHY_COMPLETION[: _HEALTHY_COMPLETION.index(removed)]

        with pytest.raises(ValueError, match=expected):
            _GATE.scan_repo(_tree(tmp_path, completion=completion))

    def test_an_enum_that_declares_nothing_raises(self, tmp_path: Path) -> None:
        completion = _HEALTHY_COMPLETION.replace(
            '    ALL_FAILED = "all_failed"\n'
            '    SKELETON_FAILED = "skeleton_failed"\n'
            '    EVALUATION_UNMET = "evaluation_unmet"\n',
            "    pass\n",
        )

        with pytest.raises(ValueError, match="declares no members"):
            _GATE.scan_repo(_tree(tmp_path, completion=completion))
