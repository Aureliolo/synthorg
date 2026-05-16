"""Unit tests for the committed git-hook wrappers in ``scripts/git-hooks/``.

These pin the contract between the venv-agnostic wrappers and pre-commit:

* the wrapper resolves the venv from the *calling worktree's* root
  (``git rev-parse --show-toplevel``), never a baked path;
* it exports ``UV_FROZEN=1`` so neither it nor any inner ``uv run``
  hook entry can rewrite ``uv.lock``;
* it execs ``uv run --frozen --project <ROOT> python -m pre_commit
  hook-impl --config=.pre-commit-config.yaml --hook-type=<type>
  --hook-dir <ROOT>/scripts/git-hooks -- <args>``.

The argv shape is pre-commit's public ``hook-impl`` CLI contract. If a
pre-commit major bump changes it, these tests fail loudly here instead
of silently breaking every worktree's push.

The tests run the real scripts with a fake ``git`` and fake ``uv`` on
``PATH`` (recording shims), so no real venv or pre-commit run is needed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_HOOKS_DIR = _REPO_ROOT / "scripts" / "git-hooks"

_BASH = shutil.which("bash")
_BASH_AVAILABLE = pytest.mark.skipif(_BASH is None, reason="bash not available")

_FAKE_TOPLEVEL = "/fake/worktree/root"


def _make_shims(bin_dir: Path, argv_capture: Path) -> None:
    """Write fake ``git`` and ``uv`` executables into *bin_dir*.

    ``git rev-parse --show-toplevel`` -> the synthetic root.
    ``uv`` -> append ``UV_FROZEN`` then one argv token per line to
    *argv_capture*, then exit 0 (replacing the real exec target).
    """
    git_shim = bin_dir / "git"
    git_shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "rev-parse" ] && [ "$2" = "--show-toplevel" ]; then\n'
        f'  echo "{_FAKE_TOPLEVEL}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    uv_shim = bin_dir / "uv"
    uv_shim.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "UV_FROZEN=${{UV_FROZEN-}}" >> "{argv_capture}"\n'
        f'for tok in "$@"; do echo "$tok" >> "{argv_capture}"; done\n'
        "exit 0\n",
        encoding="utf-8",
    )
    git_shim.chmod(0o755)
    uv_shim.chmod(0o755)


def _run_hook(
    script: Path,
    args: list[str],
    tmp_path: Path,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    assert _BASH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "argv.txt"
    _make_shims(bin_dir, capture)
    import os

    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(  # noqa: S603
        [_BASH, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        encoding="utf-8",
    )
    recorded = (
        capture.read_text(encoding="utf-8").splitlines() if capture.exists() else []
    )
    return proc, recorded


@_BASH_AVAILABLE
def test_run_hook_builds_frozen_uv_argv(tmp_path: Path) -> None:
    proc, recorded = _run_hook(
        _HOOKS_DIR / "_run-hook.sh",
        ["pre-commit", "extra-arg"],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert recorded[0] == "UV_FROZEN=1"
    assert recorded[1:] == [
        "run",
        "--frozen",
        "--project",
        _FAKE_TOPLEVEL,
        "python",
        "-m",
        "pre_commit",
        "hook-impl",
        "--config=.pre-commit-config.yaml",
        "--hook-type=pre-commit",
        "--hook-dir",
        f"{_FAKE_TOPLEVEL}/scripts/git-hooks",
        "--",
        "extra-arg",
    ]


@_BASH_AVAILABLE
def test_run_hook_requires_hook_type(tmp_path: Path) -> None:
    proc, recorded = _run_hook(_HOOKS_DIR / "_run-hook.sh", [], tmp_path)
    assert proc.returncode == 1
    assert "missing hook-type" in proc.stderr
    assert recorded == []


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "hook_name",
    ["pre-commit", "pre-push", "commit-msg"],
)
def test_dispatchers_forward_their_hook_type(
    hook_name: str,
    tmp_path: Path,
) -> None:
    proc, recorded = _run_hook(_HOOKS_DIR / hook_name, ["passthrough"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert f"--hook-type={hook_name}" in recorded
    assert recorded[-2:] == ["--", "passthrough"]
