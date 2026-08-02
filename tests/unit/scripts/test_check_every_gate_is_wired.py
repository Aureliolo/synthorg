"""Tests for the gate that checks every gate is actually invoked.

The failure this prevents is silent by construction: an unwired gate keeps
its file, its tests and its documentation row, and simply stops enforcing
anything. So the cases below are mostly about the gate not being fooled
into reporting reachability it cannot actually demonstrate, and about an
exemption never outliving its reason.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    """Load the gate by path.

    Returns:
        The module. ``ModuleType.__getattr__`` is already typed ``Any``,
        so attribute access resolves without an explicit-Any opt-out.
    """
    script = _REPO_ROOT / "scripts" / "check_every_gate_is_wired.py"
    spec = importlib.util.spec_from_file_location("_check_every_gate_is_wired", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load()

_SOURCES = (
    "_reachable_from_pre_commit",
    "_reachable_from_runners",
    "_reachable_from_ci",
    "_reachable_from_agent_hooks",
)


@pytest.fixture
def tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the gate at an empty synthetic tree with no reachability."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(_MODULE, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(_MODULE, "_SCRIPTS_DIR", scripts)
    monkeypatch.setattr(_MODULE, "_ALLOWLIST", scripts / "unwired_gate_allowlist.yaml")
    for name in _SOURCES:
        monkeypatch.setattr(_MODULE, name, set)
    return scripts


def _declare(scripts: Path, *stems: str) -> None:
    for stem in stems:
        (scripts / f"{stem}.py").write_text("# gate\n", encoding="utf-8")


def _reachable(monkeypatch: pytest.MonkeyPatch, source: str, *stems: str) -> None:
    monkeypatch.setattr(_MODULE, source, lambda: set(stems))


def _allowlist(scripts: Path, body: str) -> None:
    (scripts / "unwired_gate_allowlist.yaml").write_text(body, encoding="utf-8")


class TestReachability:
    """Any one of the five wiring sources counts as wired."""

    def test_an_unwired_gate_is_reported(self, tree: Path) -> None:
        _declare(tree, "check_orphan")
        problems = _MODULE.check()
        assert len(problems) == 1
        assert "check_orphan.py is never invoked" in problems[0]

    @pytest.mark.parametrize("source", _SOURCES)
    def test_each_source_counts_as_wired(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        # An edit-time or CI-only gate is wired, just not at the push stage;
        # reporting it would train people to ignore this gate.
        _declare(tree, "check_thing")
        _reachable(monkeypatch, source, "check_thing")
        assert _MODULE.check() == []

    def test_a_gate_wired_twice_is_still_fine(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _declare(tree, "check_thing")
        _reachable(monkeypatch, "_reachable_from_ci", "check_thing")
        _reachable(monkeypatch, "_reachable_from_pre_commit", "check_thing")
        assert _MODULE.check() == []

    def test_reports_every_unwired_gate_not_just_the_first(self, tree: Path) -> None:
        _declare(tree, "check_a", "check_b", "check_c")
        assert len(_MODULE.check()) == 3


class TestAllowlist:
    """An exemption must be deliberate, justified, and self-expiring."""

    def test_an_allowlisted_gate_passes(self, tree: Path) -> None:
        _declare(tree, "check_manual")
        _allowlist(
            tree,
            "unwired_gates:\n"
            "  - script: check_manual.py\n"
            "    reason: run by hand from a runbook\n",
        )
        assert _MODULE.check() == []

    def test_an_entry_without_a_reason_does_not_exempt(self, tree: Path) -> None:
        # A bare filename is an exemption nobody has to justify, which is how
        # an allowlist becomes a place to silence the gate.
        _declare(tree, "check_manual")
        _allowlist(tree, "unwired_gates:\n  - script: check_manual.py\n")
        problems = _MODULE.check()
        assert len(problems) == 2
        assert any("exempts nothing as written" in problem for problem in problems)

    def test_a_malformed_entry_says_so(self, tree: Path) -> None:
        # Silently dropping it produces the same output as "never
        # allowlisted", sending the author to look for wiring they already
        # declared rather than at the typo in front of them.
        _declare(tree, "check_manual")
        _allowlist(
            tree,
            "unwired_gates:\n  - scirpt: check_manual.py\n    reason: typo\n",
        )
        problems = _MODULE.check()
        assert any("is not a usable exemption" in problem for problem in problems)

    def test_a_non_mapping_entry_says_so(self, tree: Path) -> None:
        _declare(tree, "check_manual")
        _allowlist(tree, "unwired_gates:\n  - check_manual.py\n")
        problems = _MODULE.check()
        assert any("is not a mapping" in problem for problem in problems)

    def test_an_entry_for_a_deleted_gate_is_reported(self, tree: Path) -> None:
        _allowlist(
            tree,
            "unwired_gates:\n  - script: check_gone.py\n    reason: removed\n",
        )
        problems = _MODULE.check()
        assert len(problems) == 1
        assert "does not exist" in problems[0]

    def test_an_entry_for_a_now_wired_gate_is_reported(
        self, tree: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The exemption has outlived its reason; leaving it would hide the
        # next time that gate falls out of the wiring.
        _declare(tree, "check_thing")
        _allowlist(
            tree,
            "unwired_gates:\n  - script: check_thing.py\n    reason: was manual\n",
        )
        _reachable(monkeypatch, "_reachable_from_ci", "check_thing")
        problems = _MODULE.check()
        assert len(problems) == 1
        assert "is now invoked" in problems[0]

    def test_a_missing_allowlist_file_is_not_an_error(self, tree: Path) -> None:
        _declare(tree, "check_thing")
        assert len(_MODULE.check()) == 1


class TestRealTree:
    """The live regression: this repository's own gates are all wired."""

    def test_the_real_repository_passes(self) -> None:
        assert _MODULE.check() == []

    def test_it_finds_the_repositorys_gates_at_all(self) -> None:
        # A glob that silently matched nothing would make every other
        # assertion here vacuously true.
        assert len(_MODULE._declared_gates()) > 50
