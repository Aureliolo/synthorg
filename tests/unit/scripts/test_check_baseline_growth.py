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


_MODULE: Any = cast("Any", _load_script_module())  # type: ignore[explicit-any]  # dynamically loaded gate module; attrs resolved by name


# ── path classification ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # Accepted shapes
        ("scripts/mock_spec_baseline.txt", True),
        ("scripts/no_magic_numbers_baseline.txt", True),
        ("scripts/_workflow_shell_git_commits_baseline.json", True),
        ("scripts/legit_baseline.json", True),
        ("scripts/_schema_drift_baseline.py", True),
        # Wrong location
        ("tests/baselines/unit_timing.json", False),
        ("README.md", False),
        # Wrong extension / shape
        ("scripts/check_mock_spec.py", False),
        ("scripts/check_no_edit_baseline.sh", False),
        ("scripts/no_baseline_at_all.txt", False),
        # py-format baseline must start with leading underscore
        ("scripts/legit_baseline.py", False),
        # Nested paths and traversal sequences must be rejected
        ("scripts/subdir/mock_spec_baseline.txt", False),
        ("scripts/../escape_baseline.txt", False),
        (r"scripts/..\escape_baseline.txt", False),
        # Disallowed character classes in basename
        ("scripts/Capitalised_Baseline.txt", False),
        ("scripts/legit-baseline.txt", False),
        ("scripts/legit_baseline.exe", False),
        # Anchored at scripts/ specifically
        ("../escape_baseline.txt", False),
        ("scriptsX/legit_baseline.txt", False),
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


def test_count_json_entries_invalid_raises() -> None:
    with pytest.raises(_MODULE.InvalidBaselineError):
        _MODULE._count_json_entries("not json")


def test_count_json_entries_no_locations_dict_falls_back_to_top_level_keys() -> None:
    """A flat-dict baseline (no ``locations`` key) falls back to top-level keys.

    The earlier sentinel of 0 created a loophole: any flat-dict format would
    return 0 for both staged and HEAD, so growth (``staged > head``) was
    never detected. Counting top-level keys instead lets the gate catch
    additions even on unconventional baseline shapes.
    """
    assert _MODULE._count_json_entries(json.dumps({"foo": "bar"})) == 1
    assert (
        _MODULE._count_json_entries(json.dumps({"a": 1, "b": 2, "c": 3, "d": 4})) == 4
    )


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
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with (
        patch.object(
            _MODULE,
            "_read_staged",
            return_value="# header\nsrc/a.py:1:1\nsrc/b.py:2:2\nsrc/c.py:3:3\n",
        ),
        patch.object(
            _MODULE,
            "_read_head",
            return_value="# header\nsrc/a.py:1:1\n",
        ),
    ):
        rc: int = _MODULE.main(
            ["check_baseline_growth.py", "scripts/fake_baseline.txt"],
        )
    assert rc == 1
    captured = capsys.readouterr()
    assert "fake_baseline.txt" in captured.err
    assert "1 -> 3" in captured.err
    assert "ALLOW_BASELINE_GROWTH=1" in captured.err


def test_main_allows_shrink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with (
        patch.object(_MODULE, "_read_staged", return_value="# header\nsrc/a.py:1:1\n"),
        patch.object(
            _MODULE,
            "_read_head",
            return_value="# header\nsrc/a.py:1:1\nsrc/b.py:2:2\nsrc/c.py:3:3\n",
        ),
    ):
        rc: int = _MODULE.main(
            ["check_baseline_growth.py", "scripts/fake_baseline.txt"],
        )
    assert rc == 0


def test_main_treats_missing_head_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with (
        patch.object(_MODULE, "_read_staged", return_value="src/a.py:1:1\n"),
        patch.object(_MODULE, "_read_head", return_value=None),
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


def test_read_head_returns_none_when_git_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        msg = "git binary not on PATH"
        raise FileNotFoundError(msg)

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    assert _MODULE._read_head("scripts/does_not_exist.txt") is None


def test_read_head_warns_on_unexpected_oserror(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        msg = "git denied"
        raise PermissionError(msg)

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    assert _MODULE._read_head("scripts/some_baseline.txt") is None
    captured = capsys.readouterr()
    assert "git show failed" in captured.err
    assert "some_baseline.txt" in captured.err


def test_main_blocks_invalid_json_baseline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with (
        patch.object(
            _MODULE,
            "_read_staged",
            return_value="{ this is not valid json ",
        ),
        patch.object(_MODULE, "_read_head", return_value="{}"),
    ):
        rc: int = _MODULE.main(
            ["check_baseline_growth.py", "scripts/_fake_baseline.json"],
        )
    assert rc == _MODULE.EXIT_INVALID_BASELINE
    captured = capsys.readouterr()
    assert "_fake_baseline.json" in captured.err
    assert "failed to parse" in captured.err


def test_main_skips_when_path_not_in_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A baseline path missing from the git index is silently skipped.

    Pre-commit cannot reach this case in practice because the file is staged,
    but ``git show :<path>`` will return non-zero (and ``_read_staged`` will
    return ``None``) for a path that is not in the index. The gate has
    nothing to compare against, so it skips.
    """
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with patch.object(_MODULE, "_read_staged", return_value=None):
        rc: int = _MODULE.main(
            ["check_baseline_growth.py", "scripts/missing_baseline.txt"],
        )
    assert rc == 0


def test_inspect_path_warns_on_corrupt_head_baseline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A corrupt HEAD baseline emits a stderr warning before falling back to 0.

    Silent fallback masked HEAD corruption; the warning makes the over-strict
    growth check visible without changing the safe behaviour (head_count=0
    still rejects any non-empty staged baseline as growth).
    """
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    with (
        patch.object(
            _MODULE,
            "_read_staged",
            return_value=json.dumps({"locations": {"a": 1}}),
        ),
        patch.object(
            _MODULE,
            "_read_head",
            return_value="{ this is not valid json ",
        ),
    ):
        rc: int = _MODULE.main(
            [
                "check_baseline_growth.py",
                "scripts/_corrupt_head_baseline.json",
            ],
        )
    assert rc == _MODULE.EXIT_GROWTH_DETECTED
    captured = capsys.readouterr()
    assert "WARNING: HEAD baseline" in captured.err
    assert "_corrupt_head_baseline.json" in captured.err
    assert "failed to parse" in captured.err


def test_main_handles_multiple_paths_mixed_states(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ALLOW_BASELINE_GROWTH", raising=False)
    staged_lookup = {
        "scripts/grew_baseline.txt": "a\nb\nc\nd\n",
        "scripts/shrank_baseline.txt": "a\n",
        "scripts/missing_baseline.txt": None,
    }
    head_lookup = {
        "scripts/grew_baseline.txt": "a\nb\n",
        "scripts/shrank_baseline.txt": "a\nb\nc\n",
        "scripts/missing_baseline.txt": None,
    }

    def fake_read_staged(path: str) -> str | None:
        return staged_lookup[path]

    def fake_read_head(path: str) -> str | None:
        return head_lookup[path]

    with (
        patch.object(_MODULE, "_read_staged", side_effect=fake_read_staged),
        patch.object(_MODULE, "_read_head", side_effect=fake_read_head),
    ):
        rc: int = _MODULE.main(
            [
                "check_baseline_growth.py",
                "scripts/grew_baseline.txt",
                "scripts/shrank_baseline.txt",
                "scripts/missing_baseline.txt",
            ],
        )
    assert rc == 1
    captured = capsys.readouterr()
    assert "grew_baseline.txt" in captured.err
    assert "shrank_baseline.txt" not in captured.err
    assert "missing_baseline.txt" not in captured.err


# ── _read_staged ────────────────────────────────────────────────


def test_read_staged_returns_none_for_path_not_in_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=128,
            stdout="",
            stderr="fatal: path 'scripts/x.txt' does not exist in the index",
        )

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    assert _MODULE._read_staged("scripts/x.txt") is None


def test_read_staged_returns_none_when_git_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        msg = "git binary not on PATH"
        raise FileNotFoundError(msg)

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    assert _MODULE._read_staged("scripts/x.txt") is None


def test_read_staged_warns_on_unexpected_oserror(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del cmd, kwargs
        msg = "git denied"
        raise PermissionError(msg)

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    assert _MODULE._read_staged("scripts/some_baseline.txt") is None
    captured = capsys.readouterr()
    assert "git show" in captured.err
    assert "some_baseline.txt" in captured.err


def test_read_staged_returns_blob_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="src/a.py:1:1\nsrc/b.py:2:2\n",
            stderr="",
        )

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    assert (
        _MODULE._read_staged("scripts/mock_spec_baseline.txt")
        == "src/a.py:1:1\nsrc/b.py:2:2\n"
    )
