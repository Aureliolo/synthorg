"""Tests for ``scripts/check_dto_types_ts_in_sync.py``.

The gate wrapper is a thin shell around ``generate_dto_types_ts.py
--check``; verify only that exit codes propagate, missing
generators are surfaced, and the subprocess invocation is wired
correctly. The generator's own behaviour is tested in
``test_generate_dto_types_ts.py``.
"""

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _import_script() -> ModuleType:
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "check_dto_types_ts_in_sync.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_dto_types_ts_in_sync",
        script,
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _import_script()
pytestmark = pytest.mark.unit


class TestMain:
    """The wrapper delegates to the generator and propagates its exit."""

    def test_exits_zero_when_generator_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded: list[list[str]] = []

        def _fake_run(
            cmd: list[str],
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            recorded.append(cmd)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="",
                stderr="",
            )

        monkeypatch.setattr(check.subprocess, "run", _fake_run)
        assert check.main() == 0
        assert len(recorded) == 1
        cmd = recorded[0]
        assert cmd[-1] == "--check"
        assert cmd[1].endswith("generate_dto_types_ts.py")

    def test_propagates_non_zero_exit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _fake_run(
            cmd: list[str],
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="drift",
            )

        monkeypatch.setattr(check.subprocess, "run", _fake_run)
        assert check.main() == 1

    def test_exits_one_when_generator_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Point ``__file__`` resolution at a tree where the generator
        # is absent. ``main`` resolves ``parents[1] / scripts / ...``,
        # so a tmp_path/scripts/ without the generator triggers the
        # missing-file branch.
        fake_script_dir = tmp_path / "scripts"
        fake_script_dir.mkdir()
        fake_wrapper = fake_script_dir / "check_dto_types_ts_in_sync.py"
        fake_wrapper.write_text("", encoding="utf-8")
        monkeypatch.setattr(check, "__file__", str(fake_wrapper))
        assert check.main() == 1
        err = capsys.readouterr().err
        assert "missing generator" in err
