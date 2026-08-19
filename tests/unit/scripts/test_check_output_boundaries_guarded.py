"""Tests for the output-boundary reachability gate.

The gate answers two questions that used to be one. An ENFORCING boundary must
still call a guard, which is the original lock. An OBSERVING boundary must also
NOT call the raising door: the post-session backstop decides nothing on
purpose, and regaining ``enforce_output_policy`` there would restore the shipped
defect (a task failed over punctuation in narration, after its peer review had
already approved the work) with nothing else in the tree to notice.

Every case is built under ``tmp_path``. Planting a file under the real ``src/``
would race the whole-tree scanners running on other xdist workers.
"""

import importlib.util
from pathlib import Path
from typing import NamedTuple, Protocol, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]

_ENFORCING_REL = "src/synthorg/tools/file_system/_output_policy_guard.py"
_OBSERVING_REL = "src/synthorg/engine/_review_oracle_gates.py"


class _Findings(NamedTuple):
    unguarded: list[str]
    deciding: list[str]
    read_errors: list[str]


class _GateModule(Protocol):
    """Subset of ``scripts/check_output_boundaries_guarded.py`` under test."""

    #: The declared boundaries, keyed by repo-relative path. Read so the
    #: fake tree mirrors whatever the gate currently declares, rather than a
    #: second list here that would drift out of step with it.
    _BOUNDARIES: dict[str, object]

    @staticmethod
    def _check(repo_root: Path) -> _Findings: ...


def _load_module() -> _GateModule:
    script_path = _REPO_ROOT / "scripts" / "check_output_boundaries_guarded.py"
    spec = importlib.util.spec_from_file_location(
        "check_output_boundaries_guarded", script_path
    )
    if spec is None or spec.loader is None:
        msg = f"could not load module spec for {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GateModule, module)


_MODULE = _load_module()


def _plant(tmp_path: Path, relative: str, source: str) -> None:
    """Write one synthetic boundary file into the fake tree."""
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _mirror_real_tree(tmp_path: Path, **overrides: str) -> Path:
    """Copy every declared boundary into *tmp_path*, overriding some sources.

    Copying the real files means a case only ever asserts about the one
    boundary it overrode; the rest pass exactly as they do in the tree.

    Returns:
        The synthetic repository root.
    """
    for relative in _BOUNDARY_PATHS:
        source = overrides.get(
            relative, (_REPO_ROOT / relative).read_text(encoding="utf-8")
        )
        _plant(tmp_path, relative, source)
    return tmp_path


_BOUNDARY_PATHS: tuple[str, ...] = tuple(_MODULE._BOUNDARIES)


class TestTheRealTreeHolds:
    def test_every_declared_boundary_passes_today(self) -> None:
        findings = _MODULE._check(_REPO_ROOT)
        assert findings.unguarded == []
        assert findings.deciding == []
        assert findings.read_errors == []


class TestEnforcingBoundaries:
    def test_a_boundary_that_drops_its_guard_fails(self, tmp_path: Path) -> None:
        root = _mirror_real_tree(
            tmp_path,
            **{_ENFORCING_REL: "def guard_written_content():\n    return None\n"},
        )
        findings = _MODULE._check(root)
        assert any(_ENFORCING_REL in entry for entry in findings.unguarded)

    def test_a_mention_in_a_comment_does_not_satisfy_it(self, tmp_path: Path) -> None:
        root = _mirror_real_tree(
            tmp_path,
            **{
                _ENFORCING_REL: (
                    "# evaluate_output_policy used to be called here\n"
                    'GUARD = "evaluate_output_policy"\n'
                )
            },
        )
        findings = _MODULE._check(root)
        assert any(_ENFORCING_REL in entry for entry in findings.unguarded)


class TestTheObservingBoundary:
    def test_it_fails_if_it_regains_the_raising_door(self, tmp_path: Path) -> None:
        root = _mirror_real_tree(
            tmp_path,
            **{
                _OBSERVING_REL: (
                    "def observe_output_policy(text, ctx):\n"
                    "    evaluate_output_policy(text, ctx)\n"
                    "    return enforce_output_policy(text, ctx)\n"
                )
            },
        )
        findings = _MODULE._check(root)
        assert any(_OBSERVING_REL in entry for entry in findings.deciding)
        assert findings.unguarded == []

    def test_it_still_has_to_observe(self, tmp_path: Path) -> None:
        root = _mirror_real_tree(
            tmp_path,
            **{_OBSERVING_REL: "def observe_output_policy(text, ctx):\n    return\n"},
        )
        findings = _MODULE._check(root)
        assert any(_OBSERVING_REL in entry for entry in findings.unguarded)
        assert findings.deciding == []

    def test_only_the_observing_boundary_is_held_to_the_absence(
        self, tmp_path: Path
    ) -> None:
        # An enforcing boundary raising is the point of it, so the same call
        # in the same shape must be silent there.
        root = _mirror_real_tree(
            tmp_path,
            **{
                _ENFORCING_REL: (
                    "def guard_written_content(text, ctx):\n"
                    "    return enforce_output_policy(text, ctx)\n"
                )
            },
        )
        findings = _MODULE._check(root)
        assert findings.deciding == []


class TestFailClosed:
    def test_an_unreadable_boundary_is_reported(self, tmp_path: Path) -> None:
        root = _mirror_real_tree(tmp_path)
        (root / _ENFORCING_REL).unlink()
        findings = _MODULE._check(root)
        assert any(_ENFORCING_REL in entry for entry in findings.read_errors)

    def test_an_unparseable_boundary_is_reported(self, tmp_path: Path) -> None:
        root = _mirror_real_tree(tmp_path, **{_ENFORCING_REL: "def broken(:\n"})
        findings = _MODULE._check(root)
        assert any(_ENFORCING_REL in entry for entry in findings.read_errors)
