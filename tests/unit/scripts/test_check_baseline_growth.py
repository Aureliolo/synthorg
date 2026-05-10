"""Unit tests for ``scripts/check_baseline_growth.py``.

Loads the script as a module so its private helpers are callable
without spawning subprocesses.
"""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_baseline_growth.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_baseline_growth",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE: Any = cast("Any", _load_script_module())


# ── path classification ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("scripts/mock_spec_baseline.txt", True),
        ("scripts/no_magic_numbers_baseline.txt", True),
        ("scripts/_workflow_shell_git_commits_baseline.json", True),
        ("scripts/_schema_drift_baseline.py", True),
        ("scripts/check_mock_spec.py", False),
        ("scripts/check_no_edit_baseline.sh", False),
        ("scripts/no_baseline_at_all.txt", False),
        ("tests/baselines/unit_timing.json", False),
        ("scripts/subdir/mock_spec_baseline.txt", False),
        ("README.md", False),
    ],
)
def test_is_baseline_path(path: str, expected: bool) -> None:
    assert _MODULE._is_baseline_path(path) is expected


# ── entry counting ──────────────────────────────────────────────


def test_count_text_entries_skips_comments_and_blanks() -> None:
    text = (
        "# header line\n"
        "# another comment\n"
        "\n"
        "src/a.py:10:5\n"
        "src/b.py:20:7\n"
        "  \n"
        "src/c.py:30:9\n"
    )
    assert _MODULE._count_text_entries(text) == 3


def test_count_json_entries_locations_dict() -> None:
    text = json.dumps(
        {"description": "x", "locations": {"a": 1, "b": 2, "c": 3}},
    )
    assert _MODULE._count_json_entries(text) == 3


def test_count_json_entries_locations_list() -> None:
    text = json.dumps({"locations": ["a", "b"]})
    assert _MODULE._count_json_entries(text) == 2


def test_count_json_entries_top_level_list() -> None:
    text = json.dumps([1, 2, 3, 4])
    assert _MODULE._count_json_entries(text) == 4


def test_count_json_entries_invalid_returns_negative() -> None:
    assert _MODULE._count_json_entries("not json") == -1


def test_count_json_entries_no_locations_dict() -> None:
    assert _MODULE._count_json_entries(json.dumps({"foo": "bar"})) == 0


# ── classification ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("scripts/foo_baseline.txt", ".txt"),
        ("scripts/foo_baseline.json", ".json"),
        ("scripts/_foo_baseline.py", ".py"),
    ],
)
def test_classify(path: str, expected: str) -> None:
    assert _MODULE._classify(path) == expected


# ── main: end-to-end behaviour ──────────────────────────────────


def test_main_returns_zero_on_no_baseline_paths() -> None:
    rc: int = _MODULE.main(["check_baseline_growth.py", "src/foo.py"])
    assert rc == 0


def test_main_bypass_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_BASELINE_GROWTH", "1")
    rc: int = _MODULE.main(
        ["check_baseline_growth.py", "scripts/mock_spec_baseline.txt"],
    )
    assert rc == 0


def test_main_detects_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = tmp_path / "scripts" / "fake_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(
        "# header\nsrc/a.py:1:1\nsrc/b.py:2:2\nsrc/c.py:3:3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_MODULE, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with patch.object(
        _MODULE,
        "_read_head",
        return_value="# header\nsrc/a.py:1:1\n",
    ):
        rc: int = _MODULE.main(
            ["check_baseline_growth.py", "scripts/fake_baseline.txt"],
        )
    assert rc == 1
    captured = capsys.readouterr()
    assert "fake_baseline.txt" in captured.err
    assert "1 -> 3" in captured.err
    assert "ALLOW_BASELINE_GROWTH=1" in captured.err


def test_main_allows_shrink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = tmp_path / "scripts" / "fake_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(
        "# header\nsrc/a.py:1:1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_MODULE, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with patch.object(
        _MODULE,
        "_read_head",
        return_value="# header\nsrc/a.py:1:1\nsrc/b.py:2:2\nsrc/c.py:3:3\n",
    ):
        rc: int = _MODULE.main(
            ["check_baseline_growth.py", "scripts/fake_baseline.txt"],
        )
    assert rc == 0


def test_main_treats_missing_head_as_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = tmp_path / "scripts" / "new_baseline.txt"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("src/a.py:1:1\n", encoding="utf-8")
    monkeypatch.setattr(_MODULE, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with patch.object(
        _MODULE,
        "_read_head",
        return_value=None,
    ):
        rc: int = _MODULE.main(
            ["check_baseline_growth.py", "scripts/new_baseline.txt"],
        )
    assert rc == 1


def test_read_head_returns_none_for_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=128,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    assert _MODULE._read_head("scripts/does_not_exist.txt") is None
