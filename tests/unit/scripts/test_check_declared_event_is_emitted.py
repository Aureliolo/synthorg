"""Unit tests for ``scripts/check_declared_event_is_emitted.py``.

The gate exists because #2888 measured 247 of 4,357 event constants never
emitted outside the module that declares them. A regex or a scan that counts
docstring mentions as references makes exactly this defect invisible, so the
tests below are built around that failure mode: a constant mentioned only in
a comment or docstring must still fail as unemitted.

Tests load the script via :mod:`importlib` and call its private helpers
directly, matching the pattern in ``test_check_no_synthetic_cost_owner.py``.
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
    """Strip every ``GIT_*`` env var so the gate's ``git ls-files`` subprocess
    cannot escape the ``tmp_path`` sandbox and read the live repo.
    """
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_declared_event_is_emitted.py"


class _HitView(Protocol):
    """Structural view of the script's private ``_Hit`` class."""

    rel: str
    lineno: int
    name: str

    def message(self) -> str: ...


class _ScriptModule(Protocol):
    """Subset of the script's surface the tests exercise."""

    @staticmethod
    def _declared_constants(
        events_root: Path, project_root: Path
    ) -> dict[str, tuple[str, int, int]]: ...
    @staticmethod
    def _referenced_names(roots: list[Path], project_root: Path) -> set[str]: ...
    @staticmethod
    def _scan(project_root: Path) -> list[_HitView]: ...
    @staticmethod
    def main(argv: list[str] | None = None) -> int: ...


def _load_script() -> _ScriptModule:
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "_check_declared_event_is_emitted",
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


def _init_repo(tmp_path: Path) -> Path:
    """Build a minimal git-tracked tree with the gate's expected layout."""
    import subprocess

    (tmp_path / "src" / "synthorg" / "observability" / "events").mkdir(parents=True)
    events_dir = tmp_path / "src" / "synthorg" / "observability" / "events"
    (events_dir / "persistence").mkdir()
    (events_dir / "__init__.py").write_text("", encoding="utf-8")
    (events_dir / "persistence" / "__init__.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],  # noqa: S607
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],  # noqa: S607
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)  # noqa: S607
    subprocess.run(
        ["git", "commit", "-q", "-m", "wip"],  # noqa: S607
        cwd=tmp_path,
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com"},
    )


def test_declared_constants_collects_final_and_bare_annotations(
    tmp_path: Path,
) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        "from typing import Final\n\n"
        'SAMPLE_FINAL: Final[str] = "sample.final"\n'
        'SAMPLE_BARE: str = "sample.bare"\n'
        '__all__ = ["SAMPLE_FINAL", "SAMPLE_BARE"]\n',
    )
    _commit(root)
    declared = _MODULE._declared_constants(
        root / "src" / "synthorg" / "observability" / "events", root
    )
    assert set(declared) == {"SAMPLE_FINAL", "SAMPLE_BARE"}


def test_declared_constants_recurse_into_persistence_subpackage(
    tmp_path: Path,
) -> None:
    root = _init_repo(tmp_path)
    _write(
        root
        / "src"
        / "synthorg"
        / "observability"
        / "events"
        / "persistence"
        / "nested.py",
        "from typing import Final\n\n"
        'PERSISTENCE_NESTED_SAVED: Final[str] = "persistence.nested.saved"\n',
    )
    _commit(root)
    declared = _MODULE._declared_constants(
        root / "src" / "synthorg" / "observability" / "events", root
    )
    assert "PERSISTENCE_NESTED_SAVED" in declared


def test_docstring_mention_does_not_count_as_a_reference(tmp_path: Path) -> None:
    """The exact flaw the issue records in one tracing agent's scan."""
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_DEAD: Final[str] = "sample.dead"\n',
    )
    _write(
        root / "src" / "synthorg" / "consumer.py",
        '"""Docstring mentioning SAMPLE_DEAD, which must not count."""\n\n'
        "# SAMPLE_DEAD is also mentioned in a comment.\n"
        "def run() -> None:\n"
        "    pass\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "SAMPLE_DEAD" in names


def test_real_import_reference_clears_the_constant(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_LIVE: Final[str] = "sample.live"\n',
    )
    _write(
        root / "src" / "synthorg" / "consumer.py",
        "from synthorg.observability.events.sample import SAMPLE_LIVE\n\n"
        "def run() -> None:\n"
        "    print(SAMPLE_LIVE)\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "SAMPLE_LIVE" not in names


def test_evals_reference_clears_the_constant(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        "from typing import Final\n\n"
        'SAMPLE_EVAL_ONLY: Final[str] = "sample.eval_only"\n',
    )
    _write(
        root / "evals" / "consumer.py",
        "from synthorg.observability.events.sample import SAMPLE_EVAL_ONLY\n\n"
        "def run() -> None:\n"
        "    print(SAMPLE_EVAL_ONLY)\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "SAMPLE_EVAL_ONLY" not in names


def test_scripts_reference_clears_the_constant(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        "from typing import Final\n\n"
        'SAMPLE_SCRIPT_ONLY: Final[str] = "sample.script_only"\n',
    )
    _write(
        root / "scripts" / "consumer.py",
        "from synthorg.observability.events.sample import SAMPLE_SCRIPT_ONLY\n\n"
        "print(SAMPLE_SCRIPT_ONLY)\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "SAMPLE_SCRIPT_ONLY" not in names


def test_test_only_reference_does_not_clear_the_constant(tmp_path: Path) -> None:
    """A constant only the test suite names is dead, matching the 37 shipped
    in that state."""
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        "from typing import Final\n\n"
        'SAMPLE_TEST_ONLY: Final[str] = "sample.test_only"\n',
    )
    _write(
        root / "tests" / "test_sample.py",
        "from synthorg.observability.events.sample import SAMPLE_TEST_ONLY\n\n"
        "def test_it() -> None:\n"
        "    assert SAMPLE_TEST_ONLY == 'sample.test_only'\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "SAMPLE_TEST_ONLY" in names


def test_barrel_reexport_is_not_an_emission(tmp_path: Path) -> None:
    """A constant only imported inside ``events/__init__.py`` stays dead: the
    whole events package is excluded from the reference scan, not just each
    constant's own module."""
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_BARRELED: Final[str] = "sample.barreled"\n',
    )
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "__init__.py",
        "from synthorg.observability.events.sample import SAMPLE_BARRELED\n\n"
        "__all__ = ['SAMPLE_BARRELED']\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "SAMPLE_BARRELED" in names


def test_lint_allow_marker_suppresses_a_violation(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        "from typing import Final\n\n"
        "SAMPLE_EXTERNAL: Final[str] = (\n"
        "    # lint-allow: unemitted-event -- read by value in the dashboard\n"
        '    "sample.external"\n'
        ")\n",
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "SAMPLE_EXTERNAL" not in names


def test_lint_allow_marker_without_reason_does_not_suppress(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        "from typing import Final\n\n"
        "SAMPLE_NO_REASON: Final[str] = "
        '"sample.no_reason"  # lint-allow: unemitted-event\n',
    )
    _commit(root)
    hits = _MODULE._scan(root)
    names = {h.name for h in hits}
    assert "SAMPLE_NO_REASON" in names


def test_all_list_is_not_a_declared_constant(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        "from typing import Final\n\n"
        'SAMPLE_LIVE: Final[str] = "sample.live"\n'
        '__all__ = ["SAMPLE_LIVE"]\n',
    )
    _commit(root)
    declared = _MODULE._declared_constants(
        root / "src" / "synthorg" / "observability" / "events", root
    )
    assert "__all__" not in declared


def test_clean_tree_passes(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_LIVE: Final[str] = "sample.live"\n',
    )
    _write(
        root / "src" / "synthorg" / "consumer.py",
        "from synthorg.observability.events.sample import SAMPLE_LIVE\n\n"
        "def run() -> None:\n"
        "    print(SAMPLE_LIVE)\n",
    )
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 0


def test_dirty_tree_fails(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_DEAD: Final[str] = "sample.dead"\n',
    )
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 1


def test_empty_population_fails_closed(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 2


def test_unparseable_file_fails_closed(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_LIVE: Final[str] = "sample.live"\n',
    )
    _write(
        root / "src" / "synthorg" / "consumer.py",
        "def broken(:\n",
    )
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 2


# ── baseline ────────────────────────────────────────────────────


def test_baselined_violation_passes(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_DEAD: Final[str] = "sample.dead"\n',
    )
    _write(root / "scripts" / "declared_event_baseline.txt", "SAMPLE_DEAD\n")
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 0


def test_new_violation_not_in_baseline_still_fails(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        "from typing import Final\n\n"
        'SAMPLE_BASELINED: Final[str] = "sample.baselined"\n'
        'SAMPLE_NEW: Final[str] = "sample.new"\n',
    )
    _write(root / "scripts" / "declared_event_baseline.txt", "SAMPLE_BASELINED\n")
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 1


def test_stale_baseline_entry_fails_closed(tmp_path: Path) -> None:
    """A baselined name that has since been wired or deleted must shrink the
    baseline, not sit there pre-authorising a future reuse of the same name.
    """
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_LIVE: Final[str] = "sample.live"\n',
    )
    _write(
        root / "src" / "synthorg" / "consumer.py",
        "from synthorg.observability.events.sample import SAMPLE_LIVE\n\n"
        "def run() -> None:\n"
        "    print(SAMPLE_LIVE)\n",
    )
    _write(root / "scripts" / "declared_event_baseline.txt", "SAMPLE_LIVE\n")
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 2


def test_duplicate_baseline_entry_fails_closed(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_DEAD: Final[str] = "sample.dead"\n',
    )
    _write(
        root / "scripts" / "declared_event_baseline.txt",
        "SAMPLE_DEAD\nSAMPLE_DEAD\n",
    )
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root)]) == 2


def test_update_writes_the_live_population(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _write(
        root / "src" / "synthorg" / "observability" / "events" / "sample.py",
        'from typing import Final\n\nSAMPLE_DEAD: Final[str] = "sample.dead"\n',
    )
    _commit(root)
    assert _MODULE.main(["--repo-root", str(root), "--update"]) == 0
    baseline_path = root / "scripts" / "declared_event_baseline.txt"
    assert "SAMPLE_DEAD" in baseline_path.read_text(encoding="utf-8")
    assert _MODULE.main(["--repo-root", str(root)]) == 0
